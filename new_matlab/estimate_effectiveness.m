%% Estimate Control effectiveness values
% And some other analysis
% 
% Mean to run after the debug_flight.m script

%% Plot RPM data

if(isfield(ac_data,'RPM'))

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

trange = [400 500]; % seconds

datarange(1) = find(ac_data.ROTORCRAFT_CMD.timestamp>trange(1),1,'first')-1;
datarange(2) = find(ac_data.ROTORCRAFT_CMD.timestamp>trange(2),1,'first')-1;
datarange = datarange(1):datarange(2); % valid for all signals of the same frequency
t = ac_data.ROTORCRAFT_CMD.timestamp;

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

figure; plot(refquat_t,rad2deg(theta),refquat_t,rad2deg(reftheta)); title('theta')
figure; plot(refquat_t,rad2deg(phi),refquat_t,rad2deg(refphi)); title("phi")
figure; plot(refquat_t,rad2deg(psi),refquat_t,rad2deg(refpsi)); title('psi')

%% Filter signals

cmd = [ac_data.ROTORCRAFT_CMD.cmd_roll(datarange) ac_data.ROTORCRAFT_CMD.cmd_pitch(datarange) ac_data.ROTORCRAFT_CMD.cmd_yaw(datarange) ac_data.ROTORCRAFT_CMD.cmd_thrust(datarange)];
% Pay attention to the order!! Depends on the IMU mounting
gyro = [ac_data.IMU_GYRO_SCALED.gr_alt(datarange) ac_data.IMU_GYRO_SCALED.gq_alt(datarange) -ac_data.IMU_GYRO_SCALED.gp_alt(datarange)]/180*pi;
accel = [ac_data.IMU_ACCEL_SCALED.az_alt(datarange) ac_data.IMU_ACCEL_SCALED.ay_alt(datarange) -ac_data.IMU_ACCEL_SCALED.ax_alt(datarange)];
if length(accel) > length(t)
    accel = accel(1:end-1,:);
end
accelned = quatrotate(quatinv(quat_dr),accel);
accelned(1,:) = 0;
accelned(:,3) = accelned(:,3)-9.81;

% First order actuator dynamics
motor_cutoff = 18;% rad/s, of the form A(s) = motor_cutoff/(s+motor_cutoff)
motor_first_order_dynamics_constant = 1-exp(-motor_cutoff/sf);
cmd_act_mot = filter(motor_first_order_dynamics_constant,[1, -(1-motor_first_order_dynamics_constant)], cmd,[],1);

% Use different dynamics for the servos (TODO: add rate limit)
delay_num = 0;
cmd_delay = [zeros(delay_num,4); cmd(1:end-delay_num,:)];
servo_first_order_dynamics_constant = 1-exp(-50/sf);
cmd_act_servo = filter(servo_first_order_dynamics_constant,[1, -(1-servo_first_order_dynamics_constant)], cmd_delay,[],1);

filter_freq = 2;
[b, a] = butter(2,filter_freq/(sf/2));

gyro_filt = filter(b,a,gyro,get_ic(b,a,gyro(1,:)));
cmd_filt_mot = filter(b,a,cmd_act_mot,get_ic(b,a,cmd_act_mot(1,:)));
cmd_filt_servo = filter(b,a,cmd_act_servo,get_ic(b,a,cmd_act_servo(1,:)));
accel_filt = filter(b,a,accel,get_ic(b,a,accel(1,:)));
accelned_filt = filter(b,a,accelned,get_ic(b,a,accelned(1,:)));

cmd_filtd_mot = [zeros(1,4); diff(cmd_filt_mot,1)]*sf;
cmd_filtdd_mot = [zeros(1,4); diff(cmd_filtd_mot,1)]*sf;
cmd_filtd_servo = [zeros(1,4); diff(cmd_filt_servo,1)]*sf;
cmd_filtdd_servo = [zeros(1,4); diff(cmd_filtd_servo,1)]*sf;
gyro_filtd = [zeros(1,3); diff(gyro_filt,1)]*sf;
gyro_filtdd = [zeros(1,3); diff(gyro_filtd,1)]*sf;
accel_filtd = [zeros(1,3); diff(accel_filt,1)]*sf;
accel_filtdd = [zeros(1,3); diff(accel_filtd,1)]*sf;
accelned_filtd = [zeros(1,3); diff(accelned_filt,1)]*sf;


return
%% Roll effectiveness
output_roll = gyro_filtdd(:,1);
inputs_roll = [cmd_filt_mot(:,[1 3])];

Groll = inputs_roll\output_roll;
figure;
plot(t(datarange),output_roll); hold on
plot(t(datarange),inputs_roll*Groll)
title('roll fit')

%% Pitch effectiveness
output_pitch = gyro_filtdd(:,2);
inputs_pitch = [cmd_filtd_motd(:,2)];

Gpitch = inputs_pitch\output_pitch;
% unit: deg/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_pitch); hold on
plot(t(datarange),inputs_pitch*Gpitch)
title('pitch fit')
%% Yaw effectiveness

output_yaw = gyro_filtdd(:,3);
inputs_yaw = [cmd_filtd_servo(:,3)];

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
