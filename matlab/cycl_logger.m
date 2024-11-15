clear; close all;

%%
if ispc
    %
elseif isunix
    NASBASE = '/media/ntouev/ictDrive/';
    HOME = '/home/ntouev/';
else
    disp('Platform not supported')
end

addpath(fullfile(HOME, 'paparazzi_log_parsing/matlab/math'));
addpath(fullfile(HOME, 'paparazzi_log_parsing/matlab/tools'));
addpath(fullfile(HOME, 'paparazzi_log_parsing/matlab/plotters'));

%%
% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241030_valken_ewoud/144/24_10_30__16_27_37_SD.data'));
p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241030_valken_ewoud/145/24_10_30__16_45_37_SD.data'));
% p = parselog(fullfile(NASBASE, 'Flight_logs/cyclone2_pprz/20241030_valken_ewoud/148/24_10_30__17_27_57_SD.data'));
ac_data = p.aircrafts.data;

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
figure('Name', 'Flight Mode');
plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, ac_data.ROTORCRAFT_RADIO_CONTROL.mode);

%% Plot the rotorcraft fp
figure('Name','Rotorcraft FP');
plot_rotorcraft_fp(ac_data, p.aircrafts.motors_on);

%%
figure('Name', 'Guidance INDI Hybrid');
plot_guidance_indi_hybrid(ac_data);