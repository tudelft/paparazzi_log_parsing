clear; close all;

%% load flight logs
if ispc
    NASBASE = 'U:/ictDrive/';
    MATLABBASE = 'C:/Users/entouros/Documents/MATLAB/';
elseif isunix
    NASBASE = '/media/ntouev/ictDrive/';
    MATLABBASE = '/home/ntouev/MATLAB/';
else
    disp('Platform not supported')
end

addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/math'));
addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/tools'));
addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/plotters'));

% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241024_valken_first_successful_hover/104/24_10_24__20_10_26_SD_no_GPS.data'));
% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241024_valken_first_successful_hover/105/24_10_24__20_14_07_SD_no_GPS.data'));

% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241030_valken_ewoud/143/24_10_30__15_54_02_SD.data'));
% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241030_valken_ewoud/144/24_10_30__16_27_37_SD.data'));
% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241030_valken_ewoud/145/24_10_30__16_45_37_SD.data'));
% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241030_valken_ewoud/148/24_10_30__17_27_57_SD.data'));

p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241211_valken_vaggelis/161/24_12_11__15_21_45_SD.data'));

ac_data = p.aircrafts.data;

%% load field data

NASBASE = '/media/ntouev/valken/';
MATLABBASE = '/home/ntouev/MATLAB/';

addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/math'));
addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/tools'));
addpath(fullfile(MATLABBASE, 'paparazzi_log_parsing/matlab/plotters'));

p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241211_valken_vaggelis/161/24_12_11__15_21_45_SD.data'));

ac_data = p.aircrafts.data;

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

%% Plot the imu scaled
figure('Name','IMU Scaled');
cycl_plot_imu_scaled(ac_data);

%% Plot the desired and measured Euler angles
figure('Name','Euler ZXY');
cycl_plot_eul(ac_data,'ZXY');

%% Plot rpm
figure('Name', 'RPM');
cycl_plot_rpm(ac_data);

%%
figure('Name','PPM rate');
plot(ac_data.PPM.timestamp, ac_data.PPM.ppm_rate);
xlabel('time(s)');

%%
figure('Name','Stab Attitude');
cycl_plot_stab_attitude(ac_data);

%%
figure('Name','w - wref');
cycl_plot_w(ac_data);

%%
figure('Name','wdot - wdotref')
cycl_plot_wdot(ac_data);

%%
figure('Name','Elevon deflections');
cycl_plot_defl(ac_data);

%%
figure('Name', 'RC controls');
cycl_plot_rc_controls(ac_data);

%% Plot the rotorcraft fp
figure('Name','Rotorcraft FP');
plot_rotorcraft_fp(ac_data, p.aircrafts.motors_on);

%%
figure('Name', 'Guidance INDI Hybrid');
plot_guidance_indi_hybrid(ac_data);

%% Airspeed
figure('Name', 'Airspeed');
cycl_plot_airspeed(ac_data);

%% VISUALIZE FLIGHT
% cycl_visualize_3d('Nederdrone5', ac_data, [160 200], 1, 'yaw_jump');
cycl_visualize_3d('Cyclone2', ac_data, [540 570], 1, 'movie');