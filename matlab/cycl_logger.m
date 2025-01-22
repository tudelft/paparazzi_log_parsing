clear; close all;

%% load local flight data
clear;

addpath('/home/ntouev/MATLAB/paparazzi_log_parsing/matlab/math');
addpath('/home/ntouev/MATLAB/paparazzi_log_parsing/matlab/tools');
addpath('/home/ntouev/MATLAB/paparazzi_log_parsing/matlab/plotters/');

% p = parselog('/home/ntouev/pprz_temp_logs/20241030_valken_ewoud/143/24_10_30__15_54_02_SD.data'); log_nbr = '143';
% p = parselog('/home/ntouev/pprz_temp_logs/20241030_valken_ewoud/144/24_10_30__16_27_37_SD.data'); log_nbr = '144';
% p = parselog('/home/ntouev/pprz_temp_logs/20241030_valken_ewoud/145/24_10_30__16_45_37_SD.data'); log_nbr = '145';
% p = parselog('/home/ntouev/pprz_temp_logs/20241030_valken_ewoud/148/24_10_30__17_27_57_SD.data'); log_nbr = '148';

% p = parselog('/home/ntouev/pprz_temp_logs/20241211_valken_vaggelis/161/24_12_11__15_21_45_SD.data'); log_nbr = '161';

% p = parselog('/home/ntouev/pprz_temp_logs/20241220_cybezoo_tuning/0221/22_05_01__01_59_46_SD.data'); log_nbr = '0221';
% p = parselog('/home/ntouev/pprz_temp_logs/20241220_cybezoo_tuning/0222/22_05_01__01_59_46_SD.data'); log_nbr = '0222';

% p = parselog('/home/ntouev/pprz_temp_logs/20250108_mavlab_G_testing/0228/25_01_08__12_46_39_SD.data'); log_nbr = '0228';

% p = parselog('/home/ntouev/pprz_temp_logs/20250116_cyberzoo_training/0253/25_01_16__17_55_45_SD.data'); log_nbr = '0253';

% p = parselog('/home/ntouev/pprz_temp_logs/20250117_valken/0254/25_01_17__14_22_01_SD.data'); log_nbr = '0254';
% p = parselog('/home/ntouev/pprz_temp_logs/20250117_valken/0257/25_01_17__15_36_58_SD.data'); log_nbr = '0257';

p = parselog('/home/ntouev/pprz_temp_logs/0299/25_01_22__13_55_07_SD.data'); log_nbr = 'temp';
ac_data = p.aircrafts.data;

%% Imu scaled
figure('Name','IMU Scaled');
cycl_plot_imu_scaled(ac_data);

%% Euler angles
figure('Name','Euler ZXY');
cycl_plot_eul(ac_data,'ZXY');

%% actuators
figure('Name', 'Actuators');
cycl_plot_actuators(ac_data);

%% STAB data
figure('Name','Stab Attitude');
cycl_plot_stab_attitude(ac_data);

%% W
figure('Name','w - wref');
cycl_plot_w(ac_data);

%% Wdot
figure('Name','wdot - wdotref')
cycl_plot_wdot(ac_data);

%% RC controls
figure('Name', 'RC controls');
cycl_plot_rc_controls(ac_data);

%% Speed
figure('Name', 'Speed');
cycl_plot_speed(ac_data);

%% Effectiveness Matrix
figure('Name','Effectiveness Matrix'); 
cycl_plot_eff_mat(ac_data);

%% rotorcraft fp
figure('Name','Rotorcraft FP');
plot_rotorcraft_fp(ac_data, p.aircrafts.motors_on);

%% Guidance
figure('Name', 'Guidance INDI Hybrid');
plot_guidance_indi_hybrid(ac_data);

%%
figure;
plot_rotorcraft_status(ac_data)

%% Energy
figure('Name', 'Energy');
plot_energy(ac_data);

%% VISUALIZE FLIGHT
cycl_visualize_3d('Nederdrone5', ac_data, [160 200], 1, 'yaw_jump');
cycl_visualize_3d('Cyclone2', ac_data, [540 570], 1, 'movie');

%% load sim logs
MATLABBASE = '/home/ntouev/MATLAB/';

addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/math'));
addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/tools'));
addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/plotters'));

filename = '/home/ntouev/pprz_sim_logs/ZXY_20241204-155134.csv';
data = readtable(filename, 'Delimiter', ',');
ac_data = struct();

ac_data.AHRS_REF_QUAT.timestamp = data.timestamp;
ac_data.AHRS_REF_QUAT.ref_qi = data.ref_qi;
ac_data.AHRS_REF_QUAT.ref_qx = data.ref_qx;
ac_data.AHRS_REF_QUAT.ref_qy = data.ref_qy;
ac_data.AHRS_REF_QUAT.ref_qz = data.ref_qz;
ac_data.AHRS_REF_QUAT.body_qi = data.qi;
ac_data.AHRS_REF_QUAT.body_qx = data.qx;
ac_data.AHRS_REF_QUAT.body_qy = data.qy;
ac_data.AHRS_REF_QUAT.body_qz = data.qz;

ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp = data.timestamp;
ac_data.ROTORCRAFT_RADIO_CONTROL.roll = data.rc_roll;
ac_data.ROTORCRAFT_RADIO_CONTROL.pitch = data.rc_pitch;
ac_data.ROTORCRAFT_RADIO_CONTROL.yaw = data.rc_yaw;
ac_data.ROTORCRAFT_RADIO_CONTROL.throttle = data.rc_throttle;
ac_data.ROTORCRAFT_RADIO_CONTROL.mode = data.rc_mode;