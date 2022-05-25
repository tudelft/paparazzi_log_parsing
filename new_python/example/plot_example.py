import numpy as np
from matplotlib import pyplot as plt

from parselog import parselog
from plotting_tools import *


@seaborn_style()
def pretty_plot(filename):
    ac_data = get_ac_data(filename)
    plt.figure()
    plot_commands(ac_data)


if __name__ == "__main__":
    filename = '80_01_06__06_58_24_SD.log'
    log_data = parselog(filename)

    ac_data = log_data["aircrafts"][0]["data"]
    ac_msgs = log_data["msgs"]
    print(ac_data.keys())  # check all logged messages

    # Check sub-fields of certain messages
    print(f"gyro: {ac_data['IMU_GYRO'].keys()}")
    print(f"accel: {ac_data['IMU_ACCEL'].keys()}")
    print(f"gps: {ac_data['GPS'].keys()}")

    # ==== Plot with functions from spin_plot_tools ====
    # Plot log overviews
    plot_overview(filename)
    plot_ins_overview(filename)
    # plot_energy_overview(filename)

    # Single plot
    fig = plt.figure("Attitude")
    plot_attitude(ac_data)

    # Create subplots with data of interest
    # sharex='all' to zoom in all subplots at the same time
    fig, axs = plt.subplots(2, 1, sharex='all')
    plot_accelerometer(ac_data, axs[0])
    plot_gyro(ac_data, axs[1])

    # Create a pretty plot by adding the seaborn_style() decorator
    # to any function you create
    pretty_plot(filename)

    # ========== Plot manually ==============
    act_t = ac_data["ACTUATORS"]["timestamp"]
    act_0 = ac_data["ACTUATORS"]["values"][:, 0]
    act_1 = ac_data["ACTUATORS"]["values"][:, 1]
    act_2 = ac_data["ACTUATORS"]["values"][:, 2]
    act_3 = ac_data["ACTUATORS"]["values"][:, 3]

    plt.figure("Actuators")
    plt.plot(act_t, act_0, label="channel_0")
    plt.plot(act_t, act_1, label="channel_1")
    plt.plot(act_t, act_2, label="channel_2")
    plt.plot(act_t, act_3, label="channel_3")
    plt.xlabel("Time [s]")
    plt.ylabel("PWM value [micro s]")
    plt.legend()

    # Unpack message fields directly
    gt, gp, gp_alt, gq, gq_alt, gr, gr_alt = ac_data["IMU_GYRO"].values()
    plt.figure("Gyro")
    plt.plot(gt, gp, label='p')
    plt.plot(gt, gq, label='q')
    plt.plot(gt, gr, label='r')
    plt.xlabel("Time [s]")
    plt.ylabel("Rotation [rad/s]")
    plt.legend()

    print("GPS climb unit:", ac_msgs["telemetry"]["GPS"]["fields"]["climb"]["unit"])  # Check unit
    gps_t = ac_data["GPS"]["timestamp"]
    gps_cl = ac_data["GPS"]["climb"] * 0.01  # cm/s -> m/s
    plt.figure("Climb rate")
    plt.plot(gps_t, gps_cl)
    plt.xlabel("Time [s]")
    plt.ylabel("Climb rate [m/s]")

    gyro_mask = (gt > 40) & (gt < 50)  # Filter data from specific time segment
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
