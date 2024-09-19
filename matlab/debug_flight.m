clear p ac_data;
close all;

% Add all paths
addpath('math/');
addpath('plotters/');
addpath('tools/');

% Parse the log
p = parselog('./2024_09_18_troia_25kg_groundtest/24_09_18__20_45_32_SD.data');
ac_data = p.aircrafts.data;

%% Plot the Rotorcraft Status
figure('Name','Rotorcraft Status');
plot_rotorcraft_status(ac_data)

%% Plot the rotorcraft fp
figure('Name','Rotorcraft FP');
plot_rotorcraft_fp(ac_data, p.aircrafts.motors_on);

%% Plot the imu scaled
figure('Name','IMU Scaled');
plot_imu_scaled(ac_data);

%% Plot the errors
figure('Name','Errors');
plot_errors(ac_data);

%% Plot hybrid guidance
figure('Name','Hybrid Guidance');
plot_hybrid_guidance(ac_data, p.aircrafts.motors_on);

%% Plot gps data
figure('Name','GPS Data');
plot_gps(ac_data, p.aircrafts.motors_on);

%% Plot the EKF2 data
figure('Name','EKF2');
plot_ekf2(ac_data, p.aircrafts.motors_on);

%% Plot the actuators
figure('Name','Actuators 1-5');
plot_actuators(ac_data, [1,2,3,4,5]);

%% Plot the hover loop
figure('Name','Hover Loop');
plot_hover_loop(ac_data, p.aircrafts.motors_on);

%% Plot the energy
figure('Name','Energy');
plot_energy(ac_data, p.aircrafts.motors_on);

%% Plot the Rotorcraft CMD
figure('Name','Rotorcraft CMD');
plot_rotorcraft_cmd(ac_data, p.aircrafts.motors_on);

%% Plot the desired and measured Euler angles
figure('Name','Euler ZYX');
plot_eul(ac_data,'ZYX') % choose between ZYX or ZXY

%% Plot airspeed
figure('Name','Airspeed');
plot_airspeed(ac_data)

%% Plot esc
figure('Name','ESC HOVER MAIN 12,13,14,3');
plot_esc(ac_data, [12,13,14,3])

figure('Name','ESC HOVER BUP 0,1,2,15');
plot_esc(ac_data, [0,1,2,15])

figure('Name','ESC PUSHER 4,16');
plot_esc(ac_data, [4,16])

%% Plot IMU FFT
figure('Name','IMU FFT');
plot_imu_fft(ac_data)

%% Plot IMU
figure('Name','IMU');
plot_imu(ac_data)

%% Plot indi rotwing
figure('Name','INDI Rotwing');
plot_indi_rotwing(ac_data)

%% Plot rotating wing state
figure('Name','Rotwing State');
plot_rotwing_state(ac_data, p.aircrafts.motors_on)

%% Plot powers
filtered = 1;
if filtered
    figure('Name','Powers Filtered');
else
    figure('Name','Powers');
end
plot_powers(ac_data, filtered);

%% Close empty figures
fig_array = get(0, 'Children');
for i = 1 : numel(fig_array)
   if isempty(get(fig_array(i), 'Children'))
       close(fig_array(i));
   end
end