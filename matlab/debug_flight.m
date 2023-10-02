clear p ac_data;
close all;

% Add all paths
addpath('math/');
addpath('plotters/');
addpath('tools/');

% Parse the log
p = parselog('/Volumes/TU Delft/staff-umbrella/Navy/flight_logs/2022_09_08_repmus/Nederdrone7/22_09_08__13_11_50_SD.data');
ac_data = p.aircrafts.data;

%% Plot the Rotorcraft Status
figure(1);
plot_rotorcraft_status(ac_data)

%% Plot the rotorcraft fp
figure(2);
plot_rotorcraft_fp(ac_data, p.aircrafts.motors_on);

%% Plot the imu scaled
figure(3);
plot_imu_scaled(ac_data);

%% Plot the errors
figure(4);
plot_errors(ac_data);

%% Plot hybrid guidance
figure(5);
plot_hybrid_guidance(ac_data, p.aircrafts.motors_on);

%% Plot gps data
figure(6)
plot_gps(ac_data, p.aircrafts.motors_on);

%% Plot the EKF2 data
figure(7)
plot_ekf2(ac_data, p.aircrafts.motors_on);

%% Plot the actuators
figure(8)
plot_actuators(ac_data);

%% Plot the hover loop
figure(9)
plot_hover_loop(ac_data, p.aircrafts.motors_on);

%% Plot the energy
figure(10)
plot_energy(ac_data, p.aircrafts.motors_on);

%% Plot the Rotorcraft CMD
figure(11)
plot_rotorcraft_cmd(ac_data, p.aircrafts.motors_on);

%% Plot the desired and measured Euler angles
figure(12)
plot_eul(ac_data,'ZYX') % choose between ZYX or ZXY

%% Plot airspeed
figure(13)
plot_airspeed(ac_data)

%% Close empty figures

fig_array = get(0, 'Children');
for i = 1 : numel(fig_array)
   if isempty(get(fig_array(i), 'Children'))
       close(fig_array(i));
   end
end