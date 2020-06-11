import numpy as np
from matplotlib import pyplot as plt

from parselog import parselog

if __name__ == "__main__":
    filename = '20_06_02__14_19_30.log'
    log_data = parselog(filename)

    ac_data = log_data.aircrafts[0].data
    ac_msgs = log_data.msgs
    print(ac_data.keys())  # check all logged messages

    # Check sub-fields of certain messages
    print(f"gyro: {ac_data.IMU_GYRO.keys()}")
    print(f"accel: {ac_data.IMU_ACCEL.keys()}")
    print(f"gps: {ac_data.GPS.keys()}")

    act_t = ac_data.ACTUATORS.timestamp
    act_0 = ac_data.ACTUATORS.values[:, 0]
    act_1 = ac_data.ACTUATORS.values[:, 1]
    act_2 = ac_data.ACTUATORS.values[:, 2]
    act_3 = ac_data.ACTUATORS.values[:, 3]

    plt.figure("Actuators")
    plt.plot(act_t, act_0, label="channel_0")
    plt.plot(act_t, act_1, label="channel_1")
    plt.plot(act_t, act_2, label="channel_2")
    plt.plot(act_t, act_3, label="channel_3")
    plt.xlabel("Time [s]")
    plt.ylabel("PWM value [micro s]")
    plt.legend()

    # Unpack message fields directly
    gt, gp, gp_alt, gq, gq_alt, gr, gr_alt = ac_data.IMU_GYRO.values()
    plt.figure("Gyro")
    plt.plot(gt, gp, label='p')
    plt.plot(gt, gq, label='q')
    plt.plot(gt, gr, label='r')
    plt.xlabel("Time [s]")
    plt.ylabel("Rotation [rad/s]")
    plt.legend()

    print(ac_msgs.telemetry.GPS.fields.climb.unit)  # Check unit
    gps_t = ac_data.GPS.timestamp
    gps_cl = ac_data.GPS.climb * 0.01  # cm/s -> m/s
    plt.figure("Climb rate")
    plt.plot(gps_t, gps_cl)
    plt.xlabel("Time [s]")
    plt.ylabel("Climb rate [m/s]")

    att_t = ac_data.ATTITUDE.timestamp
    # Convert to degrees
    att_phi = np.rad2deg(ac_data.ATTITUDE.phi)
    att_theta = np.rad2deg(ac_data.ATTITUDE.theta)
    att_psi = np.rad2deg(ac_data.ATTITUDE.psi)

    plt.figure("Attitude")
    plt.subplot(311)
    plt.plot(att_t, att_phi, label="phi")
    plt.xlabel("Time [s]")
    plt.ylabel("Roll [deg]")
    plt.legend()
    plt.subplot(312)
    plt.plot(att_t, att_theta, label="theta")
    plt.xlabel("Time [s]")
    plt.ylabel("Pitch [deg]")
    plt.legend()
    plt.subplot(313)
    plt.plot(att_t, att_psi, label='psi')
    plt.xlabel("Time [s]")
    plt.ylabel("Yaw [deg]")
    plt.legend()

    gyro_mask = (gt > 100) & (gt < 150)  # Filter data from specific time segment
    t_section = gt[gyro_mask]
    p_section = gp[gyro_mask]
    q_section = gq[gyro_mask]
    plt.figure("Gyro section")
    plt.plot(t_section, p_section, label="p")
    plt.plot(t_section, q_section, label="q")
    plt.xlabel("Time [s]")
    plt.ylabel("Rotation [rad/s]")
    plt.grid()
    plt.legend()

    plt.show()
