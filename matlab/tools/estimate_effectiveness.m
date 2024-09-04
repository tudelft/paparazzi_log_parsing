%% Estimate Control effectiveness values
% And some other analysis
% 
% Mean to run after the debug_flight.m script

%% Plot RPM data

if(isfield(ac_data,'ESC'))

    figure(12);
    ax1 = subplot(2,1,1);
    for i=0:5
        m = find(ac_data.ESC.motor_id == i);
        plot(ac_data.ESC.timestamp(m), ac_data.ESC.rpm(m));
        hold on;
    end
    legend('RPM1', 'RPM2', 'RPM3', 'RPM10', 'RPM11', 'RPM12');

    ax2 = subplot(2,1,2);
    for i=10:15
        m = find(ac_data.ESC.motor_id == i);
        plot(ac_data.ESC.timestamp(m), ac_data.ESC.rpm(m));
        hold on;
    end
    legend('RPM4', 'RPM5', 'RPM6', 'RPM7', 'RPM8', 'RPM9');

    sgtitle('RPM')
    linkaxes([ax1, ax2],'x')
end

%% Select data range to avoid a lot of memory usage later

trange = [456.5 459]; % seconds
trange = [340 440]; % seconds
trange = [586 592]; % seconds
% trange = [400 600]; % seconds
trange = [666 674];

datarange1 = find(ac_data.IMU_GYRO_SCALED.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.IMU_GYRO_SCALED.timestamp>trange(2),1,'first')-1;
datarange = datarange1:datarange2; % valid for all signals of the same frequency
t = ac_data.IMU_GYRO_SCALED.timestamp;

sf = 500; %Hz


%% get Euler angles (zxy order for hybrids)

quat = double([ac_data.AHRS_REF_QUAT.body_qi ac_data.AHRS_REF_QUAT.body_qx ac_data.AHRS_REF_QUAT.body_qy ac_data.AHRS_REF_QUAT.body_qz]);
refquat = double([ac_data.AHRS_REF_QUAT.ref_qi ac_data.AHRS_REF_QUAT.ref_qx ac_data.AHRS_REF_QUAT.ref_qy ac_data.AHRS_REF_QUAT.ref_qz]);
[refquat_t,irefquat_t,~] = unique(ac_data.AHRS_REF_QUAT.timestamp);
quat = quat(irefquat_t,:);
refquat = refquat(irefquat_t,:);
[psi, phi, theta] = quat2angle(quat,'ZXY');
[refpsi, refphi, reftheta] = quat2angle(refquat,'ZXY');

quat_dr = interp1(refquat_t, quat, t(datarange), 'nearest'); % Quaternion on the datarange
[~, ~, theta_dr] = quat2angle(quat_dr,'ZXY');

% figure; plot(refquat_t,rad2deg(theta),refquat_t,rad2deg(reftheta)); title('theta')
% figure; plot(refquat_t,rad2deg(phi),refquat_t,rad2deg(refphi)); title("phi")
% figure; plot(refquat_t,rad2deg(psi),refquat_t,rad2deg(refpsi)); title('psi')

%% Filter signals

cmd = double(string(ac_data.ACTUATORS.values));
cmd = cmd(datarange,:);

% This only holds for the Nederdrone
motors = [2:5, 9:14];
flaps = [7:8, 15:16];
tips = [1,6];

cmd(:,motors) = (cmd(:,motors)-600)/(8191-600)*9600;
cmd(:,flaps) = cmd(:,flaps)/6000*9600;
cmd(:,tips) = (cmd(:,tips)-1500)/(8191 - 1500)*9600;

mix_matrix = zeros(16,6);
mix_matrix(2:3,1) = 0.5;
mix_matrix(4:5,2) = 0.5;
mix_matrix(9:11,3) = 1/3;
mix_matrix(12:14,4) = 1/3;
mix_matrix(7,5) = 1;
mix_matrix(8,6) = 1;
mix_matrix(15,7) = 1;
mix_matrix(16,8) = 1;


cmd_bundle = cmd * mix_matrix;
cmd = cmd_bundle;

% This only holds for the tiltprop tailsitter UAV!
% cmd = (cmd - [1560 1350 1150 1150 1500 1500])./[-600 600 750 750 400 400]*9600;

% cmd = [ac_data.ROTORCRAFT_CMD.cmd_roll(datarange) ac_data.ROTORCRAFT_CMD.cmd_pitch(datarange) ac_data.ROTORCRAFT_CMD.cmd_yaw(datarange) ac_data.ROTORCRAFT_CMD.cmd_thrust(datarange)];
% Pay attention to the order!! Depends on the IMU mounting
% gyro = [ac_data.IMU_GYRO_SCALED.gr_alt(datarange) ac_data.IMU_GYRO_SCALED.gq_alt(datarange) -ac_data.IMU_GYRO_SCALED.gp_alt(datarange)]/180*pi;
gyro = [ac_data.IMU_GYRO_SCALED.gp_alt(datarange) ac_data.IMU_GYRO_SCALED.gq_alt(datarange) ac_data.IMU_GYRO_SCALED.gr_alt(datarange)]/180*pi;
accel = [ac_data.IMU_ACCEL_SCALED.az_alt(datarange) ac_data.IMU_ACCEL_SCALED.ay_alt(datarange) -ac_data.IMU_ACCEL_SCALED.ax_alt(datarange)];

% gyro = [ac_data.BODY_RATES_ACCEL.p ac_data.BODY_RATES_ACCEL.q ac_data.BODY_RATES_ACCEL.r];
% accel = [ac_data.BODY_RATES_ACCEL.ax_alt ac_data.BODY_RATES_ACCEL.ay_alt ac_data.BODY_RATES_ACCEL.az_alt];

if length(accel) > length(t)
    accel = accel(1:end-1,:);
end

if (isfield(ac_data, 'INS'))
    accelned = [ac_data.INS.ins_xdd_alt ac_data.INS.ins_ydd_alt ac_data.INS.ins_zdd_alt];
    accelned = accelned(datarange,:);
%     if (max(abs(ac_data.INS.timestamp(datarange) - tdr)) > 2e-4) 
%         disp('WARNING: INS and gyro timestamps may not be alligned!')
%     end
else
    accelned = quatrotate(quatinv(quat_dr),accel);
    accelned(1,:) = 0;
    accelned(:,3) = accelned(:,3)-9.81;
end

% First order actuator dynamics
motor_cutoff = 18;% rad/s, of the form A(s) = motor_cutoff/(s+motor_cutoff)
motor_first_order_dynamics_constant = 1-exp(-motor_cutoff/sf);
cmd_act_mot = filter(motor_first_order_dynamics_constant,[1, -(1-motor_first_order_dynamics_constant)], cmd,[],1);

% Use different dynamics for the servos (TODO: add rate limit)
delay_num = 0;
cmd_delay = [zeros(delay_num,4); cmd(1:end-delay_num,:)];
servo_first_order_dynamics_constant = 1-exp(-50/sf);
cmd_act_servo = filter(servo_first_order_dynamics_constant,[1, -(1-servo_first_order_dynamics_constant)], cmd_delay,[],1);

fo_const = 1-exp(-20/sf);
gyro_fo = filter(fo_const,[1, -(1-fo_const)], gyro,[],1);

filter_freq = 3;
[b, a] = butter(2,filter_freq/(sf/2));

gyro_filt = filter(b,a,gyro,get_ic(b,a,gyro(1,:)));
cmd_filt_mot = filter(b,a,cmd_act_mot,get_ic(b,a,cmd_act_mot(1,:)));
cmd_filt_servo = filter(b,a,cmd_act_servo,get_ic(b,a,cmd_act_servo(1,:)));
accel_filt = filter(b,a,accel,get_ic(b,a,accel(1,:)));
accelned_filt = filter(b,a,accelned,get_ic(b,a,accelned(1,:)));
theta_filt = filter(b,a,theta);

cmd_filtd_mot = [zeros(1,size(cmd,2)); diff(cmd_filt_mot,1)]*sf;
cmd_filtdd_mot = [zeros(1,size(cmd,2)); diff(cmd_filtd_mot,1)]*sf;
cmd_filtd_servo = [zeros(1,size(cmd,2)); diff(cmd_filt_servo,1)]*sf;
cmd_filtdd_servo = [zeros(1,size(cmd,2)); diff(cmd_filtd_servo,1)]*sf;
gyro_filtd = [zeros(1,3); diff(gyro_filt,1)]*sf;
gyro_filtdd = [zeros(1,3); diff(gyro_filtd,1)]*sf;
accel_filtd = [zeros(1,3); diff(accel_filt,1)]*sf;
accel_filtdd = [zeros(1,3); diff(accel_filtd,1)]*sf;
accelned_filtd = [zeros(1,3); diff(accelned_filt,1)]*sf;


return
%% Roll effectiveness
output_roll = gyro_filtdd(:,1);
inputs_roll = [cmd_filtd_mot(:,1)];

% output_roll = gyro_filtd(:,1);
% inputs_roll = [ones(size(gyro_filtd(:,1))) cmd_filt_mot(:,[1 3])];
% inputs_roll = [ones(size(gyro_filtd(:,1))) cmd_filt_mot(:,1)];

Groll = inputs_roll\output_roll;
figure;
plot(t(datarange),output_roll); hold on
plot(t(datarange),inputs_roll*Groll)
title('roll fit')

%% Pitch effectiveness
% output_pitch = gyro_filtdd(:,2);
% inputs_pitch = [cmd_filtd_servo(:,5:6)];

output_pitch = gyro_filtd(:,2);
inputs_pitch = [ones(size(gyro_filtd(:,2))) cmd_filt_mot(:,[1:8]) gyro_filt(:,2) theta_dr];

Gpitch = inputs_pitch\output_pitch;
% unit: deg/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_pitch); hold on
plot(t(datarange),inputs_pitch*Gpitch*2)
title('pitch fit')

%% Yaw effectiveness

% output_yaw = gyro_filtdd(:,3);
% inputs_yaw = [cmd_filtd_servo(:,3)];

output_yaw = gyro_filtd(:,3);
inputs_yaw = [ones(size(gyro_filtd(:,3))) cmd_filt_mot(:,3) cmd_filtd_mot(:,3)];

Gyaw = inputs_yaw\output_yaw;
% unit: deg/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_yaw); hold on
plot(t(datarange),inputs_yaw*Gyaw)
title('yaw fit')

%% Thrust effectiveness
output_thrust = accel_filt(:,3);
inputs_thrust = [cmd_filt_mot(:,4) ones(length(datarange),1)];

Gthrust = inputs_thrust\output_thrust;
% unit: m/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_thrust); hold on
plot(t(datarange),inputs_thrust*Gthrust)
title('thrust fit')

%% indi hybrid

figure;
hold on;
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_z)
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.speed_sp_z)
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_z)
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_z)
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_y)
% plot(ac_data.STAB_ATTITUDE_FULL_INDI.timestamp(datarange),filtfilt(b,a,accelned(:,3)))
title('z control')
legend('sp acc z','sp speed z','accned filt z','eulercmd z','eulercmdy')

%%
figure;
hold on;
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_x)
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_y)
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_z)
legend('x','y','z')
title('desired acceleration hybrid indi')

%%
figure;
hold on;
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.speed_sp_x)
plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.speed_sp_y)
% plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.speed_sp_z)


% figure;
% hold on
plot(ac_data.ROTORCRAFT_FP.timestamp,ac_data.ROTORCRAFT_FP.vnorth_alt)
plot(ac_data.ROTORCRAFT_FP.timestamp,ac_data.ROTORCRAFT_FP.veast_alt)
% plot(ac_data.ROTORCRAFT_FP.timestamp,ac_data.ROTORCRAFT_FP.vup_alt)
legend('speed sp x','speed sp y','speed x', 'speed y')

%%

% interpolate the heading on the air data time vector
heading_ad = interp1(ac_data.ROTORCRAFT_FP.timestamp, ac_data.ROTORCRAFT_FP.psi_alt, ac_data.AIR_DATA.timestamp, 'nearest');

% Tune the wind to match the airspeed and the ground speed
wind = 3;
w_angle = 120;
wx = -wind*sind(w_angle);
wy = -wind*cosd(w_angle);

% Tune the sensor scale to account for sensor scalings
sensor_scale = 1.5;

% Plot the result
figure;
hold on;
plot(sensor_scale*ac_data.AIR_DATA.airspeed.*sind(heading_ad)+wx, sensor_scale*ac_data.AIR_DATA.airspeed.*cosd(heading_ad)+wy, '.')
plot(ac_data.ROTORCRAFT_FP.veast_alt, ac_data.ROTORCRAFT_FP.vnorth_alt, '.')
legend('airspeed+wind', 'ground speed')
