import sys
from os import path, getenv

import matplotlib.pyplot as plt

# if PAPARAZZI_HOME not set, then assume the tree containing this
# file is a reasonable substitute
PPRZ_HOME = getenv("PAPARAZZI_HOME", path.normpath(path.join(path.dirname(path.abspath(__file__)), '../../../../')))
sys.path.append(PPRZ_HOME + "/sw/logalizer/python/log_parser")
sys.path.append(PPRZ_HOME + "/sw/logalizer/python/ctrl_effectiveness_estimator_doublet")

from log_parser import LogParser
from ctrl_eff_est import CtrlEffEst

parsed_log = LogParser(0, 3600)

# V25kg config
eff_estimator = CtrlEffEst( parsed_log,\
                            [0.024,0.024,0.024,0.024, 0.1, 0.1, 0.1, 0.1, 0.047],\
                            2.0,\
                            [[-5000,8191], [-5000,8191], [-5000,8191], [-5000,8191], [-6500, 6500], [-8191, 4900], [-7000, 5000], [-7000,5000], [-8191,8191]],\
                            [0,0,0,0,1,0,1,1,0],\
                            [0,1,2,3,6,5,8,9])

# V3b config
# eff_estimator = CtrlEffEst( parsed_log,\
#                             [0.02,0.02,0.02,0.02,0.1,0.1,0.1,0.1,0.024],\
#                             2.0,\
#                             [[1000,8191],[1000,8191],[1000,8191],[1000,8191],[-8191,8191],[5000,-5500], [-3250,3250], [-3250,3250], [0,8191]],\
#                             [0,0,0,0,1,0,1,1,0],\
#                             [1,2,3,4,7,6,12,13,5])

eff_estimator.get_effectiveness_values()