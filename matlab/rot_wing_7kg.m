%% Select data range to avoid a lot of memory usage later

% trange = [270 283]; % seconds
% trange = [1150 1170]; % seconds
% trange = [460 500]; % seconds
% trange = [1843 1851];
trange = [1300 1390];
    
datarange1 = find(ac_data.IMU_GYRO_SCALED.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.IMU_GYRO_SCALED.timestamp>trange(2),1,'first')-1;
datarange = datarange1:datarange2; % valid for all signals of the same frequency
t = ac_data.IMU_GYRO_SCALED.timestamp;

sf = 500; %Hz

%%

use_indi_cmd = false;
use_old_act_message = false;
if(use_indi_cmd)
    cmd = double(string(ac_data.STAB_ATTITUDE.u));
elseif use_old_act_message
    cmd_temp = double(string(ac_data.ACTUATORS.values));
    cmd = zeros(length(cmd_temp),8);
    % This only holds for the rotating wing drone
    cmd(:,1:4) = (cmd_temp(:,1:4)-1000)/(8191-1000)*9600; % motors
    cmd(:,9) = (cmd_temp(:,5)+3276)/(3605+3276)*9600; %pusher
    cmd(:,6) = (cmd_temp(:,7)+7300)/(2*7300)*9600; %elevator
    cmd(:,5) = (cmd_temp(:,8)+4000)/(2*4000)*9600; %rudder
    cmd(:,7) = (cmd_temp(:,9)+3250)/(2*3250)*9600; %aileron
    cmd(:,8) = (cmd_temp(:,10)+3250)/(2*3250)*9600; %flap right
    % cmd(:,10) = (cmd_temp(:,10)+3250)/(2*3250)*9600; %aileron left
    % cmd(:,11) = (cmd_temp(:,11)+3250)/(2*3250)*9600; %flap left
    clear cmd_tmp;
else
    cmd_temp = double(string(ac_data.ACTUATORS.values));
    cmd = zeros(length(cmd_temp),8);
    % This only holds for the rotating wing drone
    cmd(:,1:4) = cmd_temp(:,1:4); % motors
    cmd(:,9) = cmd_temp(:,5); %pusher
    cmd(:,6) = cmd_temp(:,7); %elevator
    cmd(:,5) = cmd_temp(:,8); %rudder
    cmd(:,7) = cmd_temp(:,9); %aileron
    cmd(:,8) = cmd_temp(:,10); %flap right
    % cmd(:,10) = (cmd_temp(:,10)+3250)/(2*3250)*9600; %aileron left
    % cmd(:,11) = (cmd_temp(:,11)+3250)/(2*3250)*9600; %flap left
    clear cmd_tmp;
end

gyro = [ac_data.IMU_GYRO_SCALED.gp_alt(:) ac_data.IMU_GYRO_SCALED.gq_alt(:) ac_data.IMU_GYRO_SCALED.gr_alt(:)]/180*pi;
accel = [ac_data.IMU_ACCEL_SCALED.ax_alt(:) ac_data.IMU_ACCEL_SCALED.ay_alt(:) ac_data.IMU_ACCEL_SCALED.az_alt(:)];


if length(accel) > length(t)
    accel = accel(1:end-1,:);
end

% First order actuator dynamics
motor_cutoff = 10.1;% rad/s, of the form A(s) = motor_cutoff/(s+motor_cutoff)
motor_first_order_dynamics_constant = 1-exp(-motor_cutoff/sf);
cmd_act_mot = filter(motor_first_order_dynamics_constant,[1, -(1-motor_first_order_dynamics_constant)], cmd,get_ic(motor_first_order_dynamics_constant,[1, -(1-motor_first_order_dynamics_constant)],cmd(1,:)),1);

% Use different dynamics for the servos (TODO: add rate limit)
delay_num = 0;
cmd_delay = [zeros(delay_num,4); cmd(1:end-delay_num,:)];
servo_first_order_dynamics_constant = 1-exp(-50/sf);
cmd_act_servo = filter(servo_first_order_dynamics_constant,[1, -(1-servo_first_order_dynamics_constant)], cmd_delay,[],1);

fo_const = 1-exp(-20/sf);
gyro_fo = filter(fo_const,[1, -(1-fo_const)], gyro,[],1);

filter_freq = 0.8;
[b, a] = butter(4,filter_freq/(sf/2));

num_act = size(cmd_act_mot,2);

% gyro_filt = filter(b,a,gyro,get_ic(b,a,gyro(1,:)));
% cmd_filt_mot = filter(b,a,cmd_act_mot,get_ic(b,a,cmd_act_mot(1,:)));
% cmd_filt_servo = filter(b,a,cmd_act_servo,get_ic(b,a,cmd_act_servo(1,:)));
% accel_filt = filter(b,a,accel,get_ic(b,a,accel(1,:)));
% accelned_filt = filter(b,a,accelned,get_ic(b,a,accelned(1,:)));

% cmd_d_mot = [zeros(1,num_act); diff(cmd_mot,1)]*sf;
% cmd_dd_mot = [zeros(1,num_act); diff(cmd_d_mot,1)]*sf;
cmd_d_servo = [zeros(1,num_act); diff(cmd_filt_servo,1)]*sf;
cmd_dd_servo = [zeros(1,num_act); diff(cmd_filtd_servo,1)]*sf;
gyro_d = [zeros(1,3); diff(gyro,1)]*sf;
gyro_dd = [zeros(1,3); diff(gyro_d,1)]*sf;
accel_d = [zeros(1,3); diff(accel_filt,1)]*sf;
accel_dd = [zeros(1,3); diff(accel_filtd,1)]*sf;
% accelned_filtd = [zeros(1,3); diff(accelned_filt,1)]*sf;

gyro_filt = filter(b,a,gyro,get_ic(b,a,gyro(1,:)));
gyro_d_filt = filter(b,a,gyro_d,get_ic(b,a,gyro_d(1,:)));
gyro_dd_filt = filter(b,a,gyro_dd,get_ic(b,a,gyro_dd(1,:)));

cmd_act_mot_filt = filter(b,a,cmd_act_mot,get_ic(b,a,cmd_act_mot(1,:)));

return

%% Roll effectiveness


% output_roll = gyro_filtdd(:,1);
% inputs_roll = [cmd_filtd_servo(:,7:8) gyro_filtd(:,1)];
% inputs_roll = [ones(size(gyro_filtd(:,1))) cmd_filtd_mot(:,[1 3])];

% output_roll = gyro_filtd(:,1);
output_roll = diff(gyro_d_filt(:, 1));
% inputs_roll = [ones(size(gyro_filtd(:,1))) cmd_filt_mot(:,[1:4]) 0*cmd_filt_mot(:,[2 4])];
%inputs_roll = [ones(size(gyro_filtd(:,1))) cmd_filt_mot(:,[2 4])];
inputs_roll = diff(cmd_act_mot_filt(:,2)-cmd_act_mot_filt(:,4));

Groll = inputs_roll(datarange,:)\output_roll(datarange,1);
figure;
plot(t(datarange),output_roll(datarange,:)); hold on
plot(t(datarange),inputs_roll(datarange,:)*Groll)
title('roll fit')
% figure; plot(t(datarange), inputs_roll(datarange,:))

% Compare with PPRZ
k_aileron = 5;
k_flaperon = 2.0439;
airspeed = 9;
sinr3 = sind(0)^3;
cmd_pusher_scaled = (8478) * 8181 / 9600 / 1000;

Ixx_body = 0.04780;
Ixx_wing = 0.08099;
Iyy_body = 0.7546;
Iyy_wing = 0.1949;
cosr2 = cos(ac_data.ROTATING_WING_STATE.meas_skew_angle).^2;
sinr2 = sin(ac_data.ROTATING_WING_STATE.meas_skew_angle).^2;

Ixx = Ixx_body + cosr2 * Ixx_wing + sinr2 * Iyy_wing;
Iyy = Iyy_body + sinr2 * Ixx_wing + cosr2 * Iyy_wing;

dMxdpprz = (k_aileron * airspeed^2 * sinr3) / 1000000;
eff_x_aileron = dMxdpprz / Ixx;
  % Bound(eff_x_aileron, 0, 0.005)

% disp("fit: " + Groll(1) + " compared to pprz: " + eff_x_aileron + ", which is a factor " + eff_x_aileron/Groll(1) + " different.")


%% Pitch effectiveness
% output_pitch = gyro_filtdd(datarange,2);
% inputs_pitch = [cmd_filtd_mot(datarange,1:4)];

quat = double([ac_data.AHRS_REF_QUAT.body_qi ac_data.AHRS_REF_QUAT.body_qx ac_data.AHRS_REF_QUAT.body_qy ac_data.AHRS_REF_QUAT.body_qz]);
[refquat_t,irefquat_t,~] = unique(ac_data.AHRS_REF_QUAT.timestamp);
quat = quat(irefquat_t,:);
[psi, phi, theta] = quat2angle(quat,'ZXY');
theta_dr = interp1(refquat_t, theta, t, 'nearest','extrap'); % Quaternion on the datarange
theta_filt = filter(b,a,theta_dr);

% output_pitch = gyro_filtd(datarange,2);
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filt_mot(datarange,[1:4, 9]) cmd_filt_servo(datarange,[6,7])];% 0*gyro_filt(datarange,2) theta_filt(datarange)];

output_pitch = gyro_filtdd(datarange,2);
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filt_mot(datarange,[1:4]).*cmd_filt_mot(datarange,[1:4]) cmd_filt_servo(datarange,[6,7]) gyro_filt(datarange,2)];
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filtd_mot(datarange,[1:4]).*cmd_filt_mot(datarange,[1:4]) cmd_filtd_servo(datarange,[6,7]) gyro_filtd(datarange,2) gyro_filt(datarange,2)];
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filtd_mot(datarange,[1,3]).*cmd_filt_mot(datarange,[1,3]) cmd_filtd_servo(datarange,6) gyro_filtd(datarange,2) gyro_filt(datarange,2)];
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filtd_mot(datarange,[1,3])];% cmd_filtd_servo(datarange,6) 0*gyro_filtd(datarange,2)];

inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filtd_servo(datarange,6) gyro_filtd(datarange,2) gyro_filt(datarange,2)];

Gpitch = inputs_pitch\output_pitch;
% unit: deg/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_pitch); hold on
plot(t(datarange),inputs_pitch*Gpitch)
xlabel('Time (s)'); ylabel('qdot or qdotdot')
title('pitch fit')

% Compare with PPRZ
k_elevator = [1.27655, -6, -96.0/2];
k_de_deflection = [50.0,-0.0063];
airspeed = 25;
cmd_pusher_scaled = (8478) * 8181 / 9600 / 1000;
cmd_elevator = 7250;
Iyy = 8.472+0.5385;

de = k_de_deflection(1) + k_de_deflection(2) * cmd_elevator;
dMyde = (k_elevator(1) * de * airspeed^2 + k_elevator(2) * cmd_pusher_scaled * cmd_pusher_scaled * airspeed + k_elevator(3) * airspeed^2) / 10000.;
dMydpprz = dMyde * k_de_deflection(2);
eff_elevator = dMydpprz / Iyy;

disp("fit: " + Gpitch(2) + " compared to pprz: " + eff_elevator + ", which is a factor " + eff_elevator/Gpitch(2) + " different.")


%% Yaw effectiveness

% output_yaw = gyro_filtdd(datarange,3);
% inputs_yaw = [cmd_filtd_servo(datarange,1:4)];

output_yaw = gyro_filtd(datarange,3);
inputs_yaw = [ones(size(gyro_filtd(datarange,3))) 0*cmd_filt_mot(datarange,1:4) cmd_filt_servo(datarange,5) gyro_filt(datarange,3)];

Gyaw = inputs_yaw\output_yaw;
% unit: deg/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_yaw); hold on
plot(t(datarange),inputs_yaw*Gyaw)
title('yaw fit')

% Compare with PPRZ
k_rudder = [-72.5,  -0.933, -3.24];
d_rudder_d_pprz = -0.0018;
cmd_pusher_scaled = (8478) * 8181 / 9600 / 1000;
cmd_T_mean_scaled = (0) * 8181 / 9600 / 1000;
airspeed2 = 25^2;
cosr = cosd(90);
Izz = 10.18;

dMzdr = (k_rudder(1) * cmd_pusher_scaled * cmd_T_mean_scaled + k_rudder(2) * cmd_T_mean_scaled * airspeed2 * cosr + k_rudder(3) * airspeed2) / 10000.;
dMzdpprz = dMzdr * d_rudder_d_pprz;
eff_z_rudder = dMzdpprz / Izz;

disp("fit: " + Gyaw(6) + " compared to pprz: " + eff_z_rudder + ", which is a factor " + eff_z_rudder/Gyaw(6) + " different.")

%% Thrust effectiveness
output_thrust = accel_filtd(datarange,3);
inputs_thrust = [mean(cmd_filtd_mot(datarange,1:4),2) 0*cmd_filt_servo(datarange,7) gyro_filt(datarange,2) ones(length(datarange),1)];

Gthrust = inputs_thrust\output_thrust;
% unit: m/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_thrust); hold on
plot(t(datarange),inputs_thrust*Gthrust)
title('thrust fit')

% compare with PPRZ coefficients: 
kliftwing = [-0.3358,-0.6156];
kliftfuselage = -0.05065;
k_lift_tail = -0.10169;
sinr2 = sind(90)^2;
airspeed2 = 25^2;
m = 23.66;

lift_d_wing = (kliftwing(1) + kliftwing(2) * sinr2) * airspeed2 / m;
lift_d_fuselage = kliftfuselage * airspeed2 / m;
lift_d_tail = k_lift_tail * airspeed2 / m;

lift_d = lift_d_wing + lift_d_fuselage + lift_d_tail;
% Bound(lift_d, -130., 0.);
% eff_scheduling_rot_wing_lift_d = lift_d;

disp("fit: " + Gthrust(3) + " compared to pprz: " + lift_d + ", which is a factor " + lift_d/Gthrust(3) + " different.")

%% Estimate alpha

quat = double([ac_data.AHRS_REF_QUAT.body_qi ac_data.AHRS_REF_QUAT.body_qx ac_data.AHRS_REF_QUAT.body_qy ac_data.AHRS_REF_QUAT.body_qz]);
[refquat_t,irefquat_t,~] = unique(ac_data.AHRS_REF_QUAT.timestamp);
quat = quat(irefquat_t,:);
% refquat = refquat(irefquat_t,:);
[psi, phi, theta] = quat2angle(quat,'ZXY');
% [refpsi, refphi, reftheta] = quat2angle(refquat,'ZXY');

quat_dr = interp1(ac_data.AHRS_REF_QUAT.timestamp, quat, ac_data.ROTORCRAFT_FP.timestamp, 'nearest'); % Quaternion on the datarange
air_dr = interp1(ac_data.AIR_DATA.timestamp, ac_data.AIR_DATA.airspeed, ac_data.ROTORCRAFT_FP.timestamp, 'nearest'); % Quaternion on the datarange
[~, ~, theta_dr] = quat2angle(quat_dr,'ZXY');

alpha = theta_dr - atan2(ac_data.ROTORCRAFT_FP.vup_alt,air_dr);

figure; plot(ac_data.ROTORCRAFT_FP.timestamp, rad2deg(alpha), ac_data.AIR_DATA.timestamp, ac_data.AIR_DATA.airspeed)
hold on;
plot(ac_data.AHRS_REF_QUAT.timestamp, rad2deg(theta))
legend('alpha','airspeed','theta')


%% convert pusher commands to new scaling

% Still needs to be adjusted for idle command!
% put the old coefficients:
k0 = -116.518697071689;
k1 = 1.17051409813432;
k2 = -0.00002580110593734;
% calculate the idle in pprz units
min_pprz = 1000/6372*9600;
c = (9600-min_pprz)/9600;
%caclulate new coefficients
k0_new = k0+k1*min_pprz+min_pprz^2*k2;
k1_new = k1*c + 2*min_pprz*c*k2;
k2_new = k2*c^2;
% compare two points:
k0+k1*9600+k2*9600^2
k0_new+k1_new*9600+k2_new*9600^2
k0+k1*min_pprz+k2*min_pprz^2
k0_new+k1_new*0+k2_new*0^2
