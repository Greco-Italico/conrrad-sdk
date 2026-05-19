use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple, PyList};
use std::time::{SystemTime, UNIX_EPOCH};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use crate::wal::RustTransactionWAL;

type HmacSha256 = Hmac<Sha256>;

const NONCE_TTL_S: f64 = 172800.0;

#[pyclass]
pub struct RustEscrowEngine {
    redis_client: PyObject,
    signing_key: Vec<u8>,
    burn_rate: f64,
    prefix: String,
    wal: RustTransactionWAL,
}

#[pymethods]
impl RustEscrowEngine {
    #[new]
    #[pyo3(signature = (redis_client, signing_key=None, wal_path=None, burn_rate=None, key_prefix=None))]
    pub fn new(
        _py: Python,
        redis_client: PyObject,
        signing_key: Option<&[u8]>,
        wal_path: Option<&str>,
        burn_rate: Option<f64>,
        key_prefix: Option<&str>,
    ) -> PyResult<Self> {
        let burn_rate = burn_rate.unwrap_or(0.01);
        let prefix = key_prefix.unwrap_or("kap").to_string();
        let sig_key = signing_key.unwrap_or(&[]).to_vec();
        let w_path = wal_path.unwrap_or("./kap_escrow_wal.bin");

        let wal = RustTransactionWAL::new(w_path)?;

        Ok(RustEscrowEngine {
            redis_client,
            signing_key: sig_key,
            burn_rate,
            prefix,
            wal,
        })
    }

    pub fn get_balance(&self, py: Python, agent: &str) -> PyResult<f64> {
        let key = format!("{}:wallet:{}", self.prefix, agent);
        let raw = self.redis_client.call_method1(py, "get", (key,))?;
        if raw.is_none(py) {
            Ok(0.0)
        } else {
            raw.extract::<f64>(py).or_else(|_| Ok(0.0))
        }
    }

    pub fn credit(&mut self, py: Python, agent: &str, amount: f64, memo: Option<&str>) -> PyResult<(bool, String)> {
        let memo_str = memo.unwrap_or("credit");
        if amount <= 0.0 {
            return Ok((false, "amount must be positive".to_string()));
        }

        let key = format!("{}:wallet:{}", self.prefix, agent);
        self.redis_client.call_method1(py, "incrbyfloat", (key, amount))?;

        let record = PyDict::new(py);
        record.set_item("type", "credit")?;
        record.set_item("to", agent)?;
        record.set_item("amount", amount)?;
        record.set_item("memo", memo_str)?;
        record.set_item("origin", agent)?;
        
        self.append_tx(py, record)?;

        Ok((true, "ok".to_string()))
    }
}

impl RustEscrowEngine {
    fn check_nonce(&self, py: Python, nonce: &str) -> PyResult<bool> {
        let nonce_set = format!("{}:nonces", self.prefix);
        let nonce_ts = format!("{}:nonce_ts", self.prefix);
        let added: bool = self.redis_client.call_method1(py, "sadd", (&nonce_set, nonce))?.extract(py)?;
        if added {
            let ts = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64();
            let mapping = PyDict::new(py);
            mapping.set_item(nonce, ts)?;
            self.redis_client.call_method1(py, "zadd", (&nonce_ts, mapping))?;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    fn append_tx(&mut self, py: Python, record: &PyDict) -> PyResult<()> {
        let ts = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64();
        let uuid_module = py.import("uuid")?;
        
        let event_id: String = uuid_module.call_method0("uuid4")?.call_method0("__str__")?.extract()?;
        let trace_id: String = if record.contains("trace_id")? { record.get_item("trace_id")?.unwrap().extract()? } else { uuid_module.call_method0("uuid4")?.call_method0("__str__")?.extract()? };
        let span_id: String = uuid_module.call_method0("uuid4")?.call_method0("__str__")?.extract()?;
        let origin: String = if record.contains("origin")? { record.get_item("origin")?.unwrap().extract()? } else { "node://unknown".to_string() };
        let event_type: String = record.get_item("type")?.unwrap().extract()?;

        let envelope = PyDict::new(py);
        envelope.set_item("event_id", event_id)?;
        envelope.set_item("trace_id", &trace_id)?;
        envelope.set_item("span_id", span_id)?;
        envelope.set_item("timestamp", ts)?;
        envelope.set_item("runtime", "node://escrow-engine")?;
        envelope.set_item("origin", origin)?;
        envelope.set_item("event_type", format!("escrow.{}.event", event_type))?;
        envelope.set_item("version", 1)?;
        envelope.set_item("payload", record)?;

        if !self.check_nonce(py, &trace_id)? {
            return Ok(());
        }

        if !self.signing_key.is_empty() {
            let json_module = py.import("json")?;
            let clean = PyDict::new(py);
            for (k, v) in envelope.iter() {
                let key_str: String = k.extract()?;
                if key_str != "sig" {
                    clean.set_item(k, v)?;
                }
            }
            let kwargs = PyDict::new(py);
            kwargs.set_item("sort_keys", true)?;
            kwargs.set_item("separators", (",", ":"))?;
            
            let canonical: String = json_module.call_method("dumps", (clean,), Some(kwargs))?.extract()?;
            
            let mut mac = HmacSha256::new_from_slice(&self.signing_key).unwrap();
            mac.update(canonical.as_bytes());
            let result = mac.finalize();
            let sig_hex = hex::encode(result.into_bytes());
            
            envelope.set_item("sig", sig_hex)?;
        }

        self.wal.append(py, envelope)?;
        
        let tx_log = format!("{}:tx_log", self.prefix);
        let json_module = py.import("json")?;
        let json_str: String = json_module.call_method1("dumps", (envelope,))?.extract()?;
        
        let pipe = self.redis_client.call_method0(py, "pipeline")?;
        pipe.call_method1(py, "lpush", (&tx_log, &json_str))?;
        pipe.call_method1(py, "ltrim", (&tx_log, 0, 999))?;
        pipe.call_method1(py, "publish", ("kernell:events", &json_str))?; // Enterprise Event Bus
        pipe.call_method0(py, "execute")?;

        Ok(())
    }
}
