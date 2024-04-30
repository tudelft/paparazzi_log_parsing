import sys
from os import path, getenv

import matplotlib.pyplot as plt

sys.path.append("../log_parser")
sys.path.append("../ctrl_effectiveness_estimator")

from log_parser import LogParser
from ctrl_eff_est import CtrlEffEst

parsed_log = LogParser(680,712)



# V25kg config
# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.024,0.024,0.024,0.024, 0.1, 0.1, 0.1, 0.1, 0.047],\
#                             2.0,\
#                             [0,0,0,0,1,0,1,1,0],\
#                             [0,1,2,3,6,5,8,9])

# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.04,0.04,0.04,0.04],\
#                             2.0,\
#                             [0,0,0,0,0],\
#                             [0,1,2,3])

# V3e config
eff_estimator = CtrlEffEst( parsed_log,\
                            [0.02,0.02,0.02,0.02, 0.1, 0.1, 0.1, 0.1, 0.047],\
                            2.0,\
                            [0,0,0,0,1,0,1,1,0],\
                            [0,1,2,3,4,5,6,7,8])

# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.02,0.02],\
#                             2.0,\
#                             [0,0],\
#                             [1,3])

# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.02,0.02,0.02,0.02, 0.1, 0.1, 0.1, 0.1, 0.047],\
#                             2.0,\
#                             [0,0,0,0,1,0,1,1,0],\
#                             [0,1,2,3,4,5,6,7,8])

# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.03,0.025,0.03,0.025,0.1,0.1,0.1,0.1],\
#                             2.0,\
#                             [0,0,0,0,0,1,1,1,1],\
#                             [0,1,2,3,4,5,6,7])

# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.025, 0.025],\
#                             2.0,\
#                             [0,0],\
#                             [1,3])

# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.035,0.035,0.035,0.035],\
#                             0.5,\
#                             [0,0,0,0],\
#                             [0,1,2,3])


eff_estimator.get_effectiveness_values()