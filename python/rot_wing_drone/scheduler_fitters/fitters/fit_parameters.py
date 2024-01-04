import numpy as np
import matplotlib.pyplot as plt
 
def fit_hover_motor_effectiveness(doublet_actuator, airspeed, wing_angle, roll_eff, pitch_eff, yaw_eff, Ixx_body, Iyy_body, Ixx_wing, Iyy_wing):
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

    Ixx = Ixx_body + Ixx_wing * cosr2 + Iyy_wing * sinr2
    Iyy = Iyy_body + Iyy_wing * cosr2 + Ixx_wing * sinr2

    # Construct least square matrices

    A_roll = np.vstack([cosr/Ixx]).T
    A_pitch_roll = np.vstack([sk / Iyy, sk * sk * sinr / Iyy]).T
    A_pitch = np.vstack([1./Iyy]).T

    A1_roll = A_roll[doublet_actuator==1]
    A3_roll = A_roll[doublet_actuator==3]

    A0_pitch = A_pitch[doublet_actuator==0]
    A1_pitch = A_pitch_roll[doublet_actuator==1]
    A2_pitch = A_pitch[doublet_actuator==2]
    A3_pitch = A_pitch_roll[doublet_actuator==3]

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

    print('DM_DPPRZ_HOVER_PITCH = ' + str((ls0_pitch[0] + -ls2_pitch[0]) / 2.))
    print('DM_DPPRZ_HOVER_ROLL = ' + str((-ls1_roll[0] + ls3_roll[0]) / 2.))
    print('HOVER_ROLL_PITCH_COEF = ' + str(((ls1_pitch[0] - ls3_pitch[0]) / 2.)))
    print('HOVER_ROLL_ROLL_COEF = ESTIMATOR COMING SOON, BUT NOT SO SENSITIVE SO LEAVE IT [0.0, 0.0] FOR NOW')

def fit_elevator_effectiveness(doublet_actuator, airspeed, wing_angle, cmd_af, pitch_eff, Iyy_body, Ixx_wing, Iyy_wing, k_elevator_deflection):
    cosr = np.cos(np.array(wing_angle))
    sinr = np.sin(np.array(wing_angle))
    cosr2 = cosr**2
    sinr2 = sinr**2

    de = k_elevator_deflection[0] + k_elevator_deflection[1] * cmd_af.T[5].T
    cmd_pusher_scaled = cmd_af.T[8].T * 0.000853229 # Scaled with 8181 / 9600 / 1000

    Iyy = Iyy_body + Iyy_wing * cosr2 + Ixx_wing * sinr2

    A_pitch = np.vstack([de * airspeed * airspeed * k_elevator_deflection[1] / Iyy, cmd_pusher_scaled * cmd_pusher_scaled * airspeed * k_elevator_deflection[1] / Iyy, airspeed * airspeed * k_elevator_deflection[1] / Iyy]).T
    A_pitch = A_pitch[doublet_actuator==5]
    y_pitch = pitch_eff.T[doublet_actuator==5]

    ls_pitch = np.linalg.lstsq(A_pitch, y_pitch, rcond=None)

    print('K_ELEVATOR = ' + str(ls_pitch[0] * 10000))

def fit_rudder_effectiveness(doublet_actuator, airspeed, wing_angle, cmd_af, yaw_eff, Izz, d_rudder_d_pprz):
    cosr = np.cos(np.array(wing_angle))

    cmd_pusher_scaled = cmd_af.T[8] * 0.000853229 # Scaled with 8181 / 9600 / 1000
    cmd_T_mean_scaled = (cmd_af.T[0] + cmd_af.T[1] + cmd_af.T[2] + cmd_af.T[3]) / 4. * 0.000853229 # Scaled with 8181 / 9600 / 1000

    A_yaw = np.vstack([cmd_pusher_scaled * cmd_T_mean_scaled * d_rudder_d_pprz / Izz, cmd_T_mean_scaled * airspeed * airspeed * cosr * d_rudder_d_pprz / Izz, airspeed * airspeed * d_rudder_d_pprz / Izz]).T
    A_yaw = A_yaw[doublet_actuator==4]
    y_yaw = yaw_eff.T[doublet_actuator==4]

    ls_yaw = np.linalg.lstsq(A_yaw, y_yaw, rcond=None)

    print('K_RUDDER = ' + str(ls_yaw[0] * 10000))

def fit_aileron_effectiveness(doublet_actuator, airspeed, wing_angle, roll_eff, Ixx_body, Ixx_wing, Iyy_wing):
    cosr = np.cos(np.array(wing_angle))
    sinr = np.sin(np.array(wing_angle))
    cosr2 = cosr**2
    sinr2 = sinr**2
    cosr3 = cosr**3
    sinr3 = sinr**3

    Ixx = Ixx_body + Ixx_wing * cosr2 + Iyy_wing * sinr2

    A_roll = np.vstack([airspeed * airspeed * sinr3 / Ixx]).T
    A_roll = A_roll[doublet_actuator==6]
    y_roll = roll_eff.T[doublet_actuator==6]

    ls_roll = np.linalg.lstsq(A_roll, y_roll, rcond=None)

    print('K_AILERON = ' + str(ls_roll[0] * 10000))


def fit_flaperon_effectiveness(doublet_actuator, airspeed, wing_angle, roll_eff, Ixx_body, Ixx_wing, Iyy_wing):
    cosr = np.cos(np.array(wing_angle))
    sinr = np.sin(np.array(wing_angle))
    cosr2 = cosr**2
    sinr2 = sinr**2
    cosr3 = cosr**3
    sinr3 = sinr**3

    Ixx = Ixx_body + Ixx_wing * cosr2 + Iyy_wing * sinr2

    A_roll = np.vstack([airspeed * airspeed * sinr3 / Ixx]).T
    A_roll = A_roll[doublet_actuator==7]
    y_roll = roll_eff.T[doublet_actuator==7]

    ls_roll = np.linalg.lstsq(A_roll, y_roll, rcond=None)

    print('K_FLAPERON = ' + str(ls_roll[0] * 10000))





