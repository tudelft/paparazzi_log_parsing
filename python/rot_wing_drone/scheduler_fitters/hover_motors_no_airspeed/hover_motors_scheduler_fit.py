import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Ixx_body and Iyy_body and I_wing are unknown

def fuse_doublet_dfs(df_list):
    doublet_actuator = []
    airspeed = []
    wing_angle = []
    roll_eff = []
    pitch_eff = []
    yaw_eff = []

    
    if len(df_list) > 1:
        for i in range(len(df_list)):
            doublet_actuator = np.append(doublet_actuator, df_list[i]['idx'].tolist())
            wing_angle = np.append(wing_angle, df_list[i]['wing_angle'].tolist())
            roll_eff = np.append(roll_eff, df_list[i]['roll_eff'].tolist())
            pitch_eff = np.append(pitch_eff, df_list[i]['pitch_eff'].tolist())
    else:
        doublet_actuator = np.append(doublet_actuator, df_list[0]['idx'].tolist())
        airspeed = np.append(airspeed, df_list[0]['airspeed'].tolist())
        wing_angle = np.append(wing_angle, df_list[0]['wing_angle'].tolist())
        roll_eff = np.append(roll_eff, df_list[0]['roll_eff'].tolist())
        pitch_eff = np.append(pitch_eff, df_list[0]['pitch_eff'].tolist())
        yaw_eff = np.append(yaw_eff, df_list[0]['yaw_eff'].tolist())

    data = cmd_af
    # Remove unwanted characters and split the string into individual numbers
    cleaned_data = [row.replace('[', '').replace(']', '').split() for row in data]

    # Convert strings to floats in the 2D list
    float_data = [[float(entry) for entry in row] for row in cleaned_data]

    # Convert the 2D list to a NumPy array
    numpy_array = np.array(float_data)

    return np.array(doublet_actuator), np.array(wing_angle), np.array(roll_eff), np.array(pitch_eff)


doublet1 = pd.read_csv('doublet1.csv')
doublet2 = pd.read_csv('doublet2.csv')
doublet3 = pd.read_csv('doublet3.csv')
doublet4 = pd.read_csv('doublet4.csv')

dummy = pd.read_csv('dummy.csv')

dfs_doublet = [doublet1, doublet2, doublet3, doublet4]

doublet_actuator, wing_angle, roll_eff, pitch_eff = fuse_doublet_dfs(dfs_doublet)

# Calculate values according to doublet data
sk = wing_angle
sk2 = sk**2
cosr = np.cos(np.array(wing_angle))
sinr = np.sin(np.array(wing_angle))
cosr2 = cosr**2
sinr2 = sinr**2
cosr3 = cosr**3
sinr3 = sinr**3
wing_angle_deg = np.rad2deg(wing_angle)

# Give constants from fact sheets
dTdpprz = 0.01400735181907 # N/pprz
dMdpprz = 0.000509884381486 # Nm/pprz
motor_angle = np.deg2rad(10.) 
roll_arms = [0., -0.64, 0., 0.64]
pitch_arms = [0.556, 0., -0.556, 0.]
Ixx_body = 0.2845
Iyy_body = 5.078
Ixx_wing = 0.712
Iyy_wing = 1.77
Izz = 7.051
W = 25.5 # kg

Ixx = Ixx_body + Ixx_wing * cosr2 + Iyy_wing * sinr2
Iyy = Iyy_body + Iyy_wing * cosr2 + Ixx_wing * sinr2

roll_arm = np.zeros(len(doublet_actuator))
pitch_arm = np.zeros(len(doublet_actuator))

# create roll and pitch arm array
roll_arm[doublet_actuator==0] = roll_arms[0]
roll_arm[doublet_actuator==1] = roll_arms[1] * cosr[doublet_actuator==1]
roll_arm[doublet_actuator==2] = roll_arms[2]
roll_arm[doublet_actuator==3] = roll_arms[3] * cosr[doublet_actuator==3]

pitch_arm[doublet_actuator==0] = pitch_arms[0]
pitch_arm[doublet_actuator==1] = -roll_arms[1] * sinr[doublet_actuator==1]
pitch_arm[doublet_actuator==2] = pitch_arms[2]
pitch_arm[doublet_actuator==3] = -roll_arms[3] * sinr[doublet_actuator==3]

# Construct least square matrices
A_roll = np.vstack([cosr/Ixx]).T
# A_pitch_roll = np.vstack([dTdpprz*pitch_arm/(Iyy_body + Iyy_wing * cosr2 + Ixx_wing * sinr2), dTdpprz*pitch_arm*cosr2/(Iyy_body + Ixx_wing * cosr)]).T
# A_pitch_roll = np.vstack([dTdpprz*pitch_arm/(Iyy_body + Iyy_wing * cosr2 + Ixx_wing * sinr2)]).T
A_pitch_roll = np.vstack([sk / Iyy, sk * sk * sinr / Iyy]).T
A_pitch = np.vstack([1./Iyy]).T

A1_roll = A_roll[doublet_actuator==1]
A3_roll = A_roll[doublet_actuator==3]

A0_pitch = A_pitch[doublet_actuator==0]
A1_pitch = A_pitch_roll[doublet_actuator==1]
# A1_pitch = A_pitch[doublet_actuator==1]
A2_pitch = A_pitch[doublet_actuator==2]
A3_pitch = A_pitch_roll[doublet_actuator==3]
# A3_pitch = A_pitch[doublet_actuator==3]

y_roll = roll_eff.T
y_pitch = pitch_eff.T

y1_roll = y_roll[doublet_actuator==1]
y3_roll = y_roll[doublet_actuator==3]

y0_pitch = y_pitch[doublet_actuator==0]
y1_pitch = y_pitch[doublet_actuator==1]
y2_pitch = y_pitch[doublet_actuator==2]
y3_pitch = y_pitch[doublet_actuator==3]

ls1_roll = np.linalg.lstsq(A1_roll, y1_roll, rcond=None)
ls3_roll = np.linalg.lstsq(A3_roll, y3_roll, rcond=None)

ls0_pitch = np.linalg.lstsq(A0_pitch, y0_pitch, rcond=None)
ls1_pitch = np.linalg.lstsq(A1_pitch, y1_pitch, rcond=None)
ls2_pitch = np.linalg.lstsq(A2_pitch, y2_pitch, rcond=None)
ls3_pitch = np.linalg.lstsq(A3_pitch, y3_pitch, rcond=None)

coef_roll_1 = ls1_roll[0]
coef_roll_3 = ls3_roll[0]

coef_pitch_0 = ls0_pitch[0]
coef_pitch_1 = ls1_pitch[0]
coef_pitch_2 = ls2_pitch[0]
coef_pitch_3 = ls3_pitch[0]

print('coef_roll1 = ', coef_roll_1)
print('coef_roll3 = ', coef_roll_3)

print('pitch_coef0 = ', coef_pitch_0)
print('pitch_coef1 = ', coef_pitch_1)
print('pitch_coef2 = ', coef_pitch_2)
print('pitch_coef3 = ', coef_pitch_3)


# Make plots
angles = np.arange(0, 0.5 * np.pi, 0.001)
angles_deg = np.rad2deg(angles)
coss = np.cos(angles)
sins = np.sin(angles)
coss2 = coss**2
sins2 = sins**2

plt.figure('inertia vs skew')
plt.plot(angles, Ixx_body + Ixx_wing*coss2 + Iyy_wing*sins2, label='Ixx')
plt.plot(angles, Iyy_body + Iyy_wing*coss2 + Ixx_wing*sins2, label='Iyy')
plt.legend()

plt.figure('roll effectiveness vs skew')
plt.scatter(wing_angle_deg[doublet_actuator==0], roll_eff[doublet_actuator==0], label="act0")
plt.scatter(wing_angle_deg[doublet_actuator==1], roll_eff[doublet_actuator==1], label="act1")
plt.scatter(wing_angle_deg[doublet_actuator==2], roll_eff[doublet_actuator==2], label="act2")
plt.scatter(wing_angle_deg[doublet_actuator==3], roll_eff[doublet_actuator==3], label="act3")

# plt.plot(angles_deg, dTdpprz*roll_arms[1]*coss*coef_roll_1[0] + dTdpprz*roll_arms[1]*coss*sins2*coef_roll_1[1])
# plt.plot(angles_deg, dTdpprz*roll_arms[3]*coss*coef_roll_3[0] + dTdpprz*roll_arms[3]*coss*sins2*coef_roll_3[1])

plt.plot(angles_deg, coef_roll_1 * coss/(Ixx_body + coss2 * Ixx_wing + sins2 * Iyy_wing), label="act1")
plt.plot(angles_deg, coef_roll_3 * coss/(Ixx_body + coss2 * Ixx_wing + sins2 * Iyy_wing), label="act3")

plt.legend()

plt.figure('pitch effectiveness vs skew')
plt.scatter(wing_angle_deg[doublet_actuator==0], pitch_eff[doublet_actuator==0], label="act0")
plt.scatter(wing_angle_deg[doublet_actuator==1], pitch_eff[doublet_actuator==1], label="act1")
plt.scatter(wing_angle_deg[doublet_actuator==2], pitch_eff[doublet_actuator==2], label="act2")
plt.scatter(wing_angle_deg[doublet_actuator==3], pitch_eff[doublet_actuator==3], label="act3")

plt.plot(angles_deg, coef_pitch_0/(Iyy_body + sins2 * Ixx_wing + coss2 * Iyy_wing), label="act0")
plt.plot(angles_deg, ((coef_pitch_1[0] * angles + coef_pitch_1[1] * angles * angles * sins)/(Iyy_body + sins2 * Ixx_wing + coss2 * Iyy_wing)), label="act1")
plt.plot(angles_deg, coef_pitch_2/(Iyy_body + sins2 * Ixx_wing + coss2 * Iyy_wing), label="act2")
plt.plot(angles_deg, ((coef_pitch_3[0] * angles + coef_pitch_3[1] * angles * angles * sins)/(Iyy_body + sins2 * Ixx_wing + coss2 * Iyy_wing)), label="act3")

# plt.plot(angles_deg, dTdpprz*pitch_arms[0]/(Iyy_body + coss2 * Iyy_wing + sins2 * Ixx_wing))
# plt.plot(angles_deg, (-dTdpprz*roll_arms[1]*sins*coef_pitch_1[0] -dTdpprz*roll_arms[1]*sins*coss*coef_pitch_1[1])/(Iyy_body + coss2 * Iyy_wing + sins2 * Ixx_wing))
# plt.plot(angles_deg, dTdpprz*pitch_arms[2]/(Iyy_body + coss2 * Iyy_wing + sins2 * Iyy_wing))
# plt.plot(angles_deg, (-dTdpprz*roll_arms[3]*sins*coef_pitch_3[0] -dTdpprz*roll_arms[3]*sins*coss*coef_pitch_3[1])/(Iyy_body + coss2 * Iyy_wing + sins2 * Iyy_wing))

plt.show()