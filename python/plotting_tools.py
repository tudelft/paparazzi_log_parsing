import os
from functools import wraps

import numpy as np
from matplotlib import pyplot as plt
import seaborn as sns
import pickle

from parselog import parselog


def savefig_decorator(fig_name: str = None):

    def decorator(func):

        @wraps(func)
        def wrapper(*args, **kwargs):
            plt.rcParams.update({'font.size': 16})
            func(*args, **kwargs)
            fig = plt.gcf()
            fig.set_size_inches((20, 14), forward=False)
            fig.tight_layout()
            fig.subplots_adjust(top=0.95)

            cwd = os.getcwd()
            if fig_name is not None:
                plt.savefig(f"{cwd}{fig_name}.png", bbox_inches='tight', transparent=True, dpi=300)
            else:
                plt.savefig(f"{cwd}{fig.canvas.get_window_title()}.png", bbox_inches='tight', transparent=True, dpi=300)

        return wrapper

    return decorator


def seaborn_style():

    def decorator(func):

        @wraps(func)
        def wrapper(*args, **kwargs):
            if "context" in kwargs.keys():
                _context = kwargs["context"]
                del kwargs["context"]
            else:
                _context = "notebook"

            if "style" in kwargs.keys():
                _style = kwargs["style"]
                del kwargs["style"]
            else:
                _style = "whitegrid"

            if "params" in kwargs.keys():
                _params = kwargs["params"]
                del kwargs["params"]
            else:
                _params = None

            _default_params = {
              # "xtick.bottom": True,
              # "ytick.left": True,
              # "xtick.color": ".8",  # light gray
              # "ytick.color": ".15",  # dark gray
              "axes.spines.left": False,
              "axes.spines.bottom": False,
              "axes.spines.right": False,
              "axes.spines.top": False,
              }
            if _params is not None:
                merged_params = {**_params, **_default_params}
            else:
                merged_params = _default_params
            with sns.plotting_context(context=_context), sns.axes_style(style=_style, rc=merged_params):
                func(*args, **kwargs)
        return wrapper

    return decorator


def get_ac_data(filename: str):
    log_data = parselog(filename)
    ac_data = log_data["aircrafts"][0]["data"]
    return ac_data


def combine_masks(ms: np.ndarray):
    if len(ms) < 2:
        raise ValueError("There need to be at least two masks to combine")
    if len(ms) == 2:
        return np.ma.mask_or(ms[0], ms[1], shrink=False)
    else:
        comb_m = np.ma.mask_or(ms[0], ms[1], shrink=False)
        new_ms = np.vstack((comb_m, ms[2:]))
        return combine_masks(new_ms)


def plot_gyro(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    gt, _, gp_alt, _, gq_alt, _, gr_alt = ac_data["IMU_GYRO"].values()
    if interval is None:
        s_start = gt[0]
        s_end = gt[-1]
    else:
        s_start, s_end = interval

    gyro_mask = (gt > s_start) & (gt < s_end)
    ax.plot(gt[gyro_mask], gp_alt[gyro_mask], label='gp')
    ax.plot(gt[gyro_mask], gq_alt[gyro_mask], label='gq')
    ax.plot(gt[gyro_mask], gr_alt[gyro_mask], label='gr', zorder=1)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Rotation [deg/s]")
    ax.legend(loc=1)
    # ax.grid(which='both')

    return ax


def plot_actuators(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    act_t = ac_data["ACTUATORS"]["timestamp"]
    if interval is None:
        s_start = act_t[0]
        s_end = act_t[-1]
    else:
        s_start, s_end = interval

    act_0 = ac_data["ACTUATORS"]["values"][:, 0]
    act_1 = ac_data["ACTUATORS"]["values"][:, 1]
    act_2 = ac_data["ACTUATORS"]["values"][:, 2]
    act_3 = ac_data["ACTUATORS"]["values"][:, 3]

    act_mask = (act_t > s_start) & (act_t < s_end)

    ax.plot(act_t[act_mask], act_0[act_mask], label="Act 0", alpha=0.8)
    ax.plot(act_t[act_mask], act_1[act_mask], label="Act 1", alpha=0.8)
    ax.plot(act_t[act_mask], act_2[act_mask], label="Act 2", alpha=0.8)
    ax.plot(act_t[act_mask], act_3[act_mask], label="Act 3", alpha=0.8)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("PWM [\u03BCs]")
    ax.legend(loc="lower left")
    # ax.grid(which='both')

    return ax


def plot_magnetometer(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    mag_t = ac_data["IMU_MAG"]["timestamp"]
    if interval is None:
        s_start = mag_t[0]
        s_end = mag_t[-1]
    else:
        s_start, s_end = interval

    mag_mx = ac_data["IMU_MAG"]["mx"]
    mag_my = ac_data["IMU_MAG"]["my"]
    mag_mz = ac_data["IMU_MAG"]["mz"]
    mag_mask = (mag_t > s_start) & (mag_t < s_end)

    ax.plot(mag_t[mag_mask], mag_mx[mag_mask], label="mx")
    ax.plot(mag_t[mag_mask], mag_my[mag_mask], label="my")
    ax.plot(mag_t[mag_mask], mag_mz[mag_mask], label="mz")
    ax.legend()
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Mag")
    # ax.grid(which='both')

    return ax


def plot_accelerometer(ac_data: dict, ax=None, *, interval: tuple = None, norm=False):
    if ax is None:
        ax = plt.gca()

    imu_t, imu_ax, imu_ay, imu_az = ac_data["IMU_ACCEL"].values()
    if interval is None:
        s_start = imu_t[0]
        s_end = imu_t[-1]
    else:
        s_start, s_end = interval

    imu_mask = (imu_t > s_start) & (imu_t < s_end)
    ax.plot(imu_t[imu_mask], imu_ax[imu_mask], label='ax')
    ax.plot(imu_t[imu_mask], imu_ay[imu_mask], label='ay')
    ax.plot(imu_t[imu_mask], imu_az[imu_mask], label='az')
    if norm:
        imu_norm = np.linalg.norm(np.c_[imu_ax, imu_ay, imu_az], axis=1)
        ax.plot(imu_t[imu_mask], imu_norm[imu_mask], label="Norm")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Acceleration [m/s^2]")
    ax.legend()
    # ax.grid(which='both')

    return ax


def plot_attitude(ac_data: dict, ax=None, *, interval: tuple = None, plot_psi: bool = False):
    if ax is None:
        ax = plt.gca()

    att_t = ac_data["ATTITUDE"]["timestamp"]
    if interval is None:
        s_start = att_t[0]
        s_end = att_t[-1]
    else:
        s_start, s_end = interval

    att_phi = ac_data["ATTITUDE"]["phi"]
    att_theta = ac_data["ATTITUDE"]["theta"]
    att_psi = ac_data["ATTITUDE"]["psi"]
    att_mask = (att_t > s_start) & (att_t < s_end)

    ax.plot(att_t[att_mask], np.rad2deg(att_phi)[att_mask], label="phi")
    ax.plot(att_t[att_mask], np.rad2deg(att_theta)[att_mask], label="theta")
    if plot_psi:
        ax.plot(att_t[att_mask], np.rad2deg(att_psi)[att_mask], label="psi", zorder=1, alpha=0.8)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Angle [deg]")
    ax.legend()
    # ax.grid(which='both')

    return ax


def plot_gps_climb(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    gps_t = ac_data["GPS"]["timestamp"]
    if interval is None:
        s_start = gps_t[0]
        s_end = gps_t[-1]
    else:
        s_start, s_end = interval

    gps_cl = ac_data["GPS"]["climb"] * 0.01  # to m/s
    gps_mask = (gps_t > s_start) & (gps_t < s_end)
    ax.plot(gps_t[gps_mask], gps_cl[gps_mask])
    ax.set(xlabel="Time [s]", ylabel="Climb rate [m/s]")
    # ax.grid(which='both')

    return ax


def plot_gps_altitude(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    gps_t = ac_data["GPS"]["timestamp"]
    if interval is None:
        s_start = gps_t[0]
        s_end = gps_t[-1]
    else:
        s_start, s_end = interval

    gps_alt = ac_data["GPS"]["alt"] * 0.001  # to m
    gps_mask = (gps_t > s_start) & (gps_t < s_end)
    ax.plot(gps_t[gps_mask], gps_alt[gps_mask])
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Altitude [m]")
    # ax.grid(which='both')

    return ax


def plot_gps_speed(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    gps_t = ac_data["GPS"]["timestamp"]
    if interval is None:
        s_start = gps_t[0]
        s_end = gps_t[-1]
    else:
        s_start, s_end = interval

    gps_spd = ac_data["GPS"]["speed"] * 0.01  # to m/s
    gps_mask = (gps_t > s_start) & (gps_t < s_end)
    ax.plot(gps_t[gps_mask], gps_spd[gps_mask])
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Altitude [m]")
    # ax.grid(which='both')

    return ax


def plot_rc(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    rc_t = ac_data["RC"]["timestamp"]
    if interval is None:
        s_start = rc_t[0]
        s_end = rc_t[-1]
    else:
        s_start, s_end = interval

    rc_ch0 = ac_data["RC"]["values"][:, 0]
    rc_ch1 = ac_data["RC"]["values"][:, 1]
    rc_ch2 = ac_data["RC"]["values"][:, 2]
    rc_ch3 = ac_data["RC"]["values"][:, 3]
    rc_mask = (rc_t > s_start) & (rc_t < s_end)
    ax.plot(rc_t[rc_mask], rc_ch0[rc_mask], label="Channel 0")
    ax.plot(rc_t[rc_mask], rc_ch1[rc_mask], label="Channel 1")
    ax.plot(rc_t[rc_mask], rc_ch2[rc_mask], label="Channel 2")
    ax.plot(rc_t[rc_mask], rc_ch3[rc_mask], label="Channel 3")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Channel value")
    ax.legend(loc="lower left")
    # ax.grid(which='both')

    return ax


def plot_commands(ac_data: dict, ax=None, *, interval: tuple = None):
    if ax is None:
        ax = plt.gca()

    comm_t = ac_data["COMMANDS"]["timestamp"]
    if interval is None:
        s_start = comm_t[0]
        s_end = comm_t[-1]
    else:
        s_start, s_end = interval

    comm_0 = ac_data["COMMANDS"]["values"][:, 0]
    comm_1 = ac_data["COMMANDS"]["values"][:, 1]
    comm_2 = ac_data["COMMANDS"]["values"][:, 2]
    comm_3 = ac_data["COMMANDS"]["values"][:, 3]
    comm_mask = (comm_t > s_start) & (comm_t < s_end)

    ax.plot(comm_t[comm_mask], comm_0[comm_mask], label="Command 0")
    ax.plot(comm_t[comm_mask], comm_1[comm_mask], label="Command 1")
    ax.plot(comm_t[comm_mask], comm_2[comm_mask], label="Command 2")
    ax.plot(comm_t[comm_mask], comm_3[comm_mask], label="Command 3")
    # ax.grid(which='both')
    ax.set(xlabel="Time [s]", ylabel="Commands value")
    ax.legend()

    return ax


def plot_voltage(ac_data: dict, ax=None, *, interval: tuple = None):
    energy_t = ac_data["ENERGY"]["timestamp"]
    if interval is None:
        s_start = energy_t[0]
        s_end = energy_t[-1]
    else:
        s_start, s_end = interval

    energy_v = ac_data["ENERGY"]["voltage"]
    mask = (energy_t > s_start) & (energy_t < s_end)

    ax.plot(energy_t[mask], energy_v[mask], label="V")
    ax.set(xlabel="Time [s]", ylabel="Voltage [V]")
    ax.legend()

    return ax


def plot_current(ac_data: dict, ax=None, *, interval: tuple = None):
    energy_t = ac_data["ENERGY"]["timestamp"]
    if interval is None:
        s_start = energy_t[0]
        s_end = energy_t[-1]
    else:
        s_start, s_end = interval

    energy_c = ac_data["ENERGY"]["current"]
    mask = (energy_t > s_start) & (energy_t < s_end)

    ax.plot(energy_t[mask], energy_c[mask], label="A")
    ax.set(xlabel="Time [s]", ylabel="Current [A]")
    ax.legend()

    return ax


def plot_power(ac_data: dict, ax=None, *, interval: tuple = None):
    energy_t = ac_data["ENERGY"]["timestamp"]
    if interval is None:
        s_start = energy_t[0]
        s_end = energy_t[-1]
    else:
        s_start, s_end = interval

    energy_p = ac_data["ENERGY"]["power"]
    mask = (energy_t > s_start) & (energy_t < s_end)

    ax.plot(energy_t[mask], energy_p[mask], label="P")
    ax.set(xlabel="Time [s]", ylabel="Power [W]")
    ax.legend()

    return ax


def plot_ins_position(ac_data: dict, ax=None, *, interval: tuple = None, plot_z: bool = False):
    ins_t = ac_data["INS"]["timestamp"]
    if interval is None:
        s_start = ins_t[0]
        s_end = ins_t[-1]
    else:
        s_start, s_end = interval

    mask = (ins_t > s_start) & (ins_t < s_end)
    ins_x = ac_data["INS"]["ins_x_alt"]
    ins_y = ac_data["INS"]["ins_y_alt"]
    ins_z = ac_data["INS"]["ins_z_alt"]

    ax.plot(ins_t[mask], ins_x[mask], label="x")
    ax.plot(ins_t[mask], ins_y[mask], label="y")
    if plot_z:
        ax.plot(ins_t[mask], ins_z[mask], label="z")
    ax.set(xlabel="Time [s]", ylabel="Position [m]")
    ax.legend()

    return ax


def plot_ins_velocity(ac_data: dict, ax=None, *, interval: tuple = None, plot_z: bool = False):
    ins_t = ac_data["INS"]["timestamp"]
    if interval is None:
        s_start = ins_t[0]
        s_end = ins_t[-1]
    else:
        s_start, s_end = interval

    mask = (ins_t > s_start) & (ins_t < s_end)
    ins_xd = ac_data["INS"]["ins_xd_alt"]
    ins_yd = ac_data["INS"]["ins_yd_alt"]
    ins_zd = ac_data["INS"]["ins_zd_alt"]

    ax.plot(ins_t[mask], ins_xd[mask], label="xd")
    ax.plot(ins_t[mask], ins_yd[mask], label="yd")
    if plot_z:
        ax.plot(ins_t[mask], ins_zd[mask], label="zd")
    ax.set(xlabel="Time [s]", ylabel="Velocity [m/s]")
    ax.legend()

    return ax


def plot_ins_acceleration(ac_data: dict, ax=None, *, interval: tuple = None):
    ins_t = ac_data["INS"]["timestamp"]
    if interval is None:
        s_start = ins_t[0]
        s_end = ins_t[-1]
    else:
        s_start, s_end = interval

    mask = (ins_t > s_start) & (ins_t < s_end)
    ins_xdd = ac_data["INS"]["ins_xdd_alt"]
    ins_ydd = ac_data["INS"]["ins_ydd_alt"]
    ins_zdd = ac_data["INS"]["ins_zdd_alt"]

    ax.plot(ins_t[mask], ins_xdd[mask], label="x")
    ax.plot(ins_t[mask], ins_ydd[mask], label="y")
    ax.plot(ins_t[mask], ins_zdd[mask], label="z")
    ax.set(xlabel="Time [s]", ylabel="Acceleration [m/s^2]")
    ax.legend()

    return ax


def plot_ins_position_2d(ac_data: dict, ax=None, *, interval: tuple = None):
    ins_t = ac_data["INS"]["timestamp"]
    if interval is None:
        s_start = ins_t[0]
        s_end = ins_t[-1]
    else:
        s_start, s_end = interval

    mask = (ins_t > s_start) & (ins_t < s_end)
    ins_x = ac_data["INS"]["ins_x_alt"]
    ins_y = ac_data["INS"]["ins_y_alt"]

    ax.plot(ins_x[mask], ins_y[mask], label="x")
    ax.set(xlabel="X [m]", ylabel="Y [m]")
    ax.legend()

    return ax


def plot_datalink_report(filename):
    ac_data = get_ac_data(filename)

    dlk_t = ac_data["DATALINK_REPORT"]["timestamp"]
    dlk_u_lt = ac_data["DATALINK_REPORT"]["uplink_lost_time"]
    dlk_u_nb = ac_data["DATALINK_REPORT"]["uplink_nb_msgs"]
    dlk_d_nb = ac_data["DATALINK_REPORT"]["downlink_nb_msgs"]
    dlk_d_rt = ac_data["DATALINK_REPORT"]["downlink_nb_msgs"]
    dlk_u_rt = ac_data["DATALINK_REPORT"]["downlink_nb_msgs"]
    dlk_d_ovr = ac_data["DATALINK_REPORT"]["downlink_nb_msgs"]

    fig, axs = plt.subplots(2, 2)
    axs[0, 0].plot(dlk_t, dlk_u_lt, label="downlink lost time")
    axs[0, 0].legend()

    axs[0, 1].plot(dlk_t, dlk_d_nb, label="downlink_nb_msgs")
    axs[0, 1].plot(dlk_t, dlk_u_nb, label="uplink_nb_msgs")
    axs[0, 1].legend()

    axs[1, 0].plot(dlk_t, dlk_d_rt, label="downlink rate")
    axs[1, 0].plot(dlk_t, dlk_u_rt, label="uplink rate")
    axs[1, 0].legend()

    axs[1, 1].plot(dlk_t, dlk_d_ovr, label="downlink_ovrn")
    axs[1, 1].legend()


@seaborn_style()
def plot_ins_overview(filename: str, interval: tuple = None, plot_z: bool = False):
    ac_data = get_ac_data(filename)

    fig, axs = plt.subplots(3, 1, sharex='all')

    plot_ins_position(ac_data, axs[0], interval=interval, plot_z=plot_z)
    plot_ins_velocity(ac_data, axs[1], interval=interval, plot_z=plot_z)
    plot_ins_acceleration(ac_data, axs[2], interval=interval)


@seaborn_style()
def plot_energy_overview(filename: str, interval: tuple = None):
    ac_data = get_ac_data(filename)

    fig, axs = plt.subplots(2, 2, sharex='all')

    plot_actuators(ac_data, axs[0, 0], interval=interval)
    plot_voltage(ac_data, axs[0, 1], interval=interval)
    plot_current(ac_data, axs[1, 0], interval=interval)
    plot_power(ac_data, axs[1, 1], interval=interval)


@seaborn_style()
def plot_overview(filename: str, interval: tuple = None):
    ac_data = get_ac_data(filename)

    fig, axs = plt.subplots(4, 2, sharex='all')

    plot_attitude(ac_data, axs[0, 0], interval=interval, plot_psi=True)
    plot_accelerometer(ac_data, axs[0, 1], interval=interval)
    plot_gyro(ac_data, axs[1, 0], interval=interval)
    plot_magnetometer(ac_data, axs[1, 1], interval=interval)
    plot_rc(ac_data, axs[2, 0], interval=interval)
    plot_actuators(ac_data, axs[2, 1], interval=interval)
    plot_gps_climb(ac_data, axs[3, 0], interval=interval)
    plot_gps_altitude(ac_data, axs[3, 1], interval=interval)


def z_frame_transformation(phi: np.ndarray, theta: np.ndarray, psi: np.ndarray):
    # From Flight Dynamics Reader
    t_vec = np.array([[np.cos(phi) * np.sin(theta) * np.cos(psi) + np.sin(phi) * np.sin(psi)],
                      [np.cos(phi) * np.sin(theta) * np.sin(psi) - np.sin(phi) * np.cos(psi)],
                      [np.cos(phi) * np.cos(theta)]])
    return t_vec


def pickle_log_file(log_name: str, fields: list, filename: str = "log_dump"):
    ac_data = get_ac_data(log_name)

    data = {}
    for field in fields:
        if field in ac_data.keys():
            data[field] = {}
            for subfield in ac_data[field]:
                data[field][subfield] = ac_data[field][subfield]

    dest_dir = os.getcwd()

    with open(dest_dir + filename + ".pickle", 'wb') as p:
        pickle.dump(data, p)


def pickle_ac_data(ac_data: dict, filename: str = "log_dump", fields: list = None):
    if fields is None:
        fields = ac_data.keys()
    data = {}
    for field in fields:
        if field in ac_data.keys():
            data[field] = {}
            for subfield in ac_data[field]:
                data[field][subfield] = ac_data[field][subfield]

    dest_dir = os.getcwd()

    with open(dest_dir + filename + ".pickle", 'wb') as p:
        pickle.dump(data, p)


if __name__ == "__main__":
    pass
