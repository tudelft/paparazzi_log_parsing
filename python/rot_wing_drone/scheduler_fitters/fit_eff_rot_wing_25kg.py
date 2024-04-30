import numpy as np
import pandas as pd
import effectiveness_df_fuser as fuser
import fitters.fit_parameters as fit
import matplotlib.pyplot as plt

# Give constants from defined in effectiveness scheduler section of airframe file
# Ixx_body = 0.2845
# Iyy_body = 5.078
# Ixx_wing = 0.712
# Iyy_wing = 1.77
# Izz = 7.051
# W = 25.5 # kg

# 25kgb
Ixx_body = 0.3953
Iyy_body = 8.472
Ixx_wing = 0.5385
Iyy_wing = 1.671
Izz = 10.18
W = 23.66 # kg


k_elevator_deflection = [50.0,-0.0063]
d_rudder_d_pprz = -0.0018

# Read doublet files
# doublet1 = pd.read_csv('doublets_25kg/doublet1.csv')
doublet1 = pd.read_csv('doublets_25kgb/doublets1.csv') # missing further actuator data
dummy = pd.read_csv('doublets_25kgb/dummy.csv')
doublet2 = pd.read_csv('doublets_25kgb/doublet2.csv') # missing further actuator data
doublet3 = pd.read_csv('doublets_25kgb/doublet_yaw_as1.csv')
# doublet3 = pd.read_csv('doublets_25kg/doublet3.csv')
# doublet4 = pd.read_csv('doublets_25kg/doublet4.csv')
#test_doublet = pd.read_csv('doublets_25kg/test_doublet.csv')

dfs_doublet = [doublet3, dummy]

doublet_actuator, airspeed, wing_angle, cmd_af, roll_eff, pitch_eff, yaw_eff = fuser.fuse_doublet_dfs(dfs_doublet)

# Perform fits

fit.fit_hover_motor_effectiveness(doublet_actuator, airspeed, wing_angle, roll_eff, pitch_eff, yaw_eff, Ixx_body, Iyy_body, Ixx_wing, Iyy_wing)
# fit.fit_elevator_effectiveness(doublet_actuator, airspeed, wing_angle, cmd_af, pitch_eff, Iyy_body, Ixx_wing, Iyy_wing, k_elevator_deflection)
fit.fit_rudder_effectiveness(doublet_actuator, airspeed, wing_angle, cmd_af, yaw_eff, Izz, d_rudder_d_pprz)
# fit.fit_aileron_effectiveness(doublet_actuator, airspeed, wing_angle, roll_eff, Ixx_body, Ixx_wing, Iyy_wing)
# fit.fit_flaperon_effectiveness(doublet_actuator, airspeed, wing_angle, roll_eff, Ixx_body, Ixx_wing, Iyy_wing)


plt.show()
