import sys
from kernell_sdk.chaos_engine import ChaosController, WALStormScenario

ctrl = ChaosController()
req_id = "test-req"
res = ctrl.run_scenario(WALStormScenario, req_id)
