clear all
close all

p = parselog('../21_08_19__15_06_55.data');
ac_data = p.aircrafts.data;

% plotting commands
show_agl = true;
show_pos = true;
show_vel = true;

% Get the important time areas
ap_in_flight = ac_data.ROTORCRAFT_STATUS.timestamp(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_in_flight))));
ap_motors_on = ac_data.ROTORCRAFT_STATUS.timestamp(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_motors_on))));
flow_takeover = ac_data.DEBUG.timestamp(find(diff(int32(ac_data.DEBUG.debug_13))));

%% Plot the AGL status
figure_agl = figure('Visible', show_agl);
figure_agl.Position = [100,100,1700,500];
set(figure_agl,'defaulttextinterpreter','latex');
plot(ac_data.LIDAR.timestamp, ac_data.LIDAR.distance); hold on;
plot(ac_data.OPTICAL_FLOW.timestamp, ac_data.OPTICAL_FLOW.distance_compensated); hold on;
plot(ac_data.GPS_INT.timestamp, (double(ac_data.GPS_INT.hmsl)/1000)+0.04); hold on;
xline(ap_motors_on,'--r');
title('AGL Estimation Accuracy - Indoor');
xlabel('Time [s]');
ylabel('$AGL$ [m]');
legend('GARMIN','L0X','OPTITRACK');

%% Plot the position status
figure_pos = figure('Visible', show_pos);
figure_pos.Position = [100,100,1700,1700];
set(figure_pos,'defaulttextinterpreter','latex');

ax1 = subplot(3,1,1);
plot(ac_data.ROTORCRAFT_FP.timestamp, double(ac_data.ROTORCRAFT_FP.north)*0.0039063); hold on;
plot(ac_data.ROTORCRAFT_FP.timestamp, double(ac_data.ROTORCRAFT_FP.carrot_north)*0.0039063); hold on;
xline(ap_motors_on,'--r'); hold on;
xline(flow_takeover,'--b');
legend('north','setpoint north');
xlabel('Time [s]');
ylabel('$P_{north}$ [m]');
title('North Coordinate Setpoint Following');

ax2 = subplot(3,1,2);
plot(ac_data.ROTORCRAFT_FP.timestamp, double(ac_data.ROTORCRAFT_FP.east)*0.0039063); hold on;
plot(ac_data.ROTORCRAFT_FP.timestamp, double(ac_data.ROTORCRAFT_FP.carrot_east)*0.0039063); hold on;
xline(ap_motors_on,'--r'); hold on;
xline(flow_takeover,'--b');
legend('east','setpoint east');
xlabel('Time [s]');
ylabel('$P_{east}$ [m]');
title('East Coordinate Setpoint Following');

ax3 = subplot(3,1,3);
plot(ac_data.ROTORCRAFT_FP.timestamp, double(ac_data.ROTORCRAFT_FP.up)*0.0039063); hold on;
plot(ac_data.ROTORCRAFT_FP.timestamp, double(ac_data.ROTORCRAFT_FP.carrot_up)*0.0039063); hold on;
xline(ap_motors_on,'--r'); hold on;
xline(flow_takeover,'--b');
legend('up','setpoint up');
xlabel('Time [s]');
ylabel('$P_{up}$ [m]');
title('Up Coordinate Setpoint Following');

sgtitle('Position Setpoint Following - Indoor')
linkaxes([ax1,ax2,ax3],'x')

%% Plot the Body Velocity Compared to the Optitrack Values

% EKF GENERATED BODY VELOCITIES

% get NED velocities from EKF
vned_a.north = double(ac_data.ROTORCRAFT_FP.vnorth)*0.0000019;
vned_a.east = double(ac_data.ROTORCRAFT_FP.veast)*0.0000019;
vned_a.up = double(ac_data.ROTORCRAFT_FP.vup)*0.0000019;

vned.north = interp1(ac_data.ROTORCRAFT_FP.timestamp, vned_a.north, ac_data.IMU_MAG_RAW.timestamp);
vned.east = interp1(ac_data.ROTORCRAFT_FP.timestamp, vned_a.east, ac_data.IMU_MAG_RAW.timestamp);
vned.up = interp1(ac_data.ROTORCRAFT_FP.timestamp, vned_a.up, ac_data.IMU_MAG_RAW.timestamp);

% get ECEF velocities from OPTITRACK
vecef_gt.north = double(ac_data.GPS_INT.ecef_xd)*0.01;
vecef_gt.east = double(ac_data.GPS_INT.ecef_yd)*0.01;
vecef_gt.up = double(ac_data.GPS_INT.ecef_zd)*0.01;

vned_gt.north = zeros(1,size(vecef_gt.north,1));
vned_gt.east = zeros(1,size(vecef_gt.north,1));
vned_gt.up = zeros(1,size(vecef_gt.north,1));

% convert from ECEF to NED
ecef.lat = deg2rad(ac_data.GPS_INT.lat_alt);
ecef.lon = deg2rad(ac_data.GPS_INT.lon_alt);

% transpose both
for i = 1:size(vecef_gt.north,1)-1
    
    DCM_ef_i = [-sin(ecef.lat(i))*cos(ecef.lon(i)) -sin(ecef.lat(i))*sin(ecef.lon(i)) cos(ecef.lat(i));
              -sin(ecef.lon(i)) cos(ecef.lon(i)) 0;
              -cos(ecef.lat(i))*cos(ecef.lon(i)) -cos(ecef.lat(i))*sin(ecef.lon(i)) -sin(ecef.lat(i))];
    
    V_ecef_i = [vecef_gt.north(i), vecef_gt.east(i), vecef_gt.up(i)];
    V_ned_i = DCM_ef_i * V_ecef_i';
    vned_gt.north(i) = V_ned_i(1);
    vned_gt.east(i) = V_ned_i(2);
end


% interpolate OPTITRACK on EKF
vned_gt_r.north = interp1(ac_data.GPS_INT.timestamp, vecef_gt.north, ac_data.IMU_MAG_RAW.timestamp);
vned_gt_r.east = interp1(ac_data.GPS_INT.timestamp, vecef_gt.east, ac_data.IMU_MAG_RAW.timestamp);
vned_gt_r.up = interp1(ac_data.GPS_INT.timestamp, vecef_gt.up, ac_data.IMU_MAG_RAW.timestamp);

% get magneto from OPTITRACK
mag.x = double(ac_data.IMU_MAG_RAW.mx);
mag.y = double(ac_data.IMU_MAG_RAW.my);
mag.z = double(ac_data.IMU_MAG_RAW.mz);

% transform NED frame for velocity to body frame - EKF
vbody.x = zeros(1,size(vned.north,1));
vbody.y = zeros(1,size(vned.north,1));

% transform NED frame for velocity to body frame - OPTITRACK
vbody_gt.x = zeros(1,size(vned_gt_r.north,1));
vbody_gt.y = zeros(1,size(vned_gt_r.north,1));

% interpolate magneto on EKF 
mag_r.x = mag.x;
mag_r.y = mag.y;
mag_r.z = mag.z;

% turn magnetometer data from adc to unit vector
max_mag.x = abs(max(mag_r.x));
max_mag.y = abs(max(mag_r.y));
max_mag.z = abs(max(mag_r.z));
max_all = max(max_mag.x, max(max_mag.y, max_mag.z));
mag_r.x = mag_r.x/max_all;
mag_r.y = mag_r.y/max_all;
mag_r.z = mag_r.z/max_all;

% transpose both
for i = 1:size(vned.north,1)-1
    heading_i = acos(mag_r.x(i));
    T_Eb_i = [cos(heading_i) sin(heading_i) 0; 
            -sin(heading_i) cos(heading_i) 0;
            0 0 1];
    V_E_i = [vned.north(i), vned.east(i), vned.up(i)];
    V_E_i_gt = [vned_gt_r.north(i), vned_gt_r.east(i), vned_gt_r.up(i)];
    V_B_i = T_Eb_i * V_E_i.';
    V_B_i_gt = T_Eb_i * V_E_i_gt.';
    vbody.x(i) = V_B_i(1);
    vbody.y(i) = V_B_i(2);
    vbody_gt.x(i) = V_B_i_gt(2);
    vbody_gt.y(i) = V_B_i_gt(1);
end

% shift velocity
% vbody_gt.x = circshift(vbody_gt.x,11);
% vbody_gt.y = circshift(vbody_gt.y,20);

figure_vel = figure('Visible', show_vel);
figure_vel.Position = [100,100,1700,1300];
set(figure_vel,'defaulttextinterpreter','latex');

ax1 = subplot(2,1,1);
plot(ac_data.IMU_MAG_RAW.timestamp, smoothdata(vbody.x)); hold on;
plot(ac_data.IMU_MAG_RAW.timestamp, smoothdata(vbody_gt.x)); hold on;
xline(ap_motors_on,'--r'); hold on;
xline(flow_takeover,'--b');
legend('EKF2','OPTITRACK');
xlabel('Time [s]');
ylabel('$V_x$ [m/s]');
title('Body Velocity Comparison X Direction');

ax2 = subplot(2,1,2);
plot(ac_data.IMU_MAG_RAW.timestamp, smoothdata(vbody.y)); hold on;
plot(ac_data.IMU_MAG_RAW.timestamp, smoothdata(vbody_gt.y)); hold on;
xline(ap_motors_on,'--r'); hold on;
xline(flow_takeover,'--b');
legend('EKF2','OPTITRACK');
xlabel('Time [s]');
ylabel('$V_y$ [m/s]');
title('Body Velocity Comparison Y Direction');

sgtitle('Body Velocity Estimation Accuracy EKF2 - Indoor')
linkaxes([ax1,ax2],'x')
