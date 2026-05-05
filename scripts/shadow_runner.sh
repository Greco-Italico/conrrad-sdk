#!/bin/bash
# Kernell OS - Shadow Mode Runner
# Entrypoint for the systemd service

# No usamos set -e para poder atrapar el error explícitamente

cd /opt/kernell-os

echo "[$(date)] Iniciando Kernell Shadow Mode Runner..."

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "[$(date)] ERROR: No se encontró el virtual environment en /opt/kernell-os/venv"
    exit 1
fi

export PYTHONPATH=/opt/kernell-os/kernell-sdk:$PYTHONPATH

# Iniciar Heartbeat en background
(
  while true; do
      echo "[$(date)] HEARTBEAT: Shadow runner alive"
      sleep 60
  done
) &
HEARTBEAT_PID=$!

echo "[$(date)] Ejecutando traffic generator..."
# En producción, esto debe ser el proxy atado al tráfico vivo, no --simulate
# python3 kernell-sdk/kernell_sdk/runtime/real_traffic_entrypoint.py
python3 kernell-sdk/kernell_sdk/tools/stability_dashboard.py --simulate --conf 0.6 --dens 0.8 || {
    echo "[$(date)] ERROR: Shadow process crashed or exited with failure code."
    kill $HEARTBEAT_PID
    exit 1
}

kill $HEARTBEAT_PID
echo "[$(date)] El proceso de Shadow Mode finalizó limpiamente. Systemd reiniciará en 5 segundos si Restart=always/on-failure."
