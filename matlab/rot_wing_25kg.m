%% Select data range to avoid a lot of memory usage later

% trange = [270 283]; % seconds
% trange = [1150 1170]; % seconds
trange = [1400 1440]; % seconds
% trange = [1843 1851];
    
datarange1 = find(ac_data.IMU_GYRO_SCALED.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.IMU_GYRO_SCALED.timestamp>trange(2),1,'first')-1;
datarange = datarange1:datarange2; % valid for all signals of the same frequency
t = ac_data.IMU_GYRO_SCALED.timestamp;

sf = 500; %Hz

%%

use_indi_cmd = true;
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
    cmd = double(string(ac_data.ACTUATORS.values));
end

gyro = [ac_data.IMU_GYRO_SCALED.gp_alt(:) ac_data.IMU_GYRO_SCALED.gq_alt(:) ac_data.IMU_GYRO_SCALED.gr_alt(:)]/180*pi;
accel = [ac_data.IMU_ACCEL_SCALED.ax_alt(:) ac_data.IMU_ACCEL_SCALED.ay_alt(:) ac_data.IMU_ACCEL_SCALED.az_alt(:)];


if length(accel) > length(t)
    accel = accel(1:end-1,:);
end

% First order actuator dynamics
motor_cutoff = 12;% rad/s, of the form A(s) = motor_cutoff/(s+motor_cutoff)
motor_first_order_dynamics_constant = 1-exp(-motor_cutoff/sf);
cmd_act_mot = filter(motor_first_order_dynamics_constant,[1, -(1-motor_first_order_dynamics_constant)], cmd,get_ic(motor_first_order_dynamics_constant,[1, -(1-motor_first_order_dynamics_constant)],cmd(1,:)),1);

% Use different dynamics for the servos (TODO: add rate limit)
delay_num = 0;
cmd_delay = [zeros(delay_num,4); cmd(1:end-delay_num,:)];
servo_first_order_dynamics_constant = 1-exp(-50/sf);
cmd_act_servo = filter(servo_first_order_dynamics_constant,[1, -(1-servo_first_order_dynamics_constant)], cmd_delay,[],1);

fo_const = 1-exp(-20/sf);
gyro_fo = filter(fo_const,[1, -(1-fo_const)], gyro,[],1);

filter_freq = 2;
[b, a] = butter(4,filter_freq/(sf/2));

num_act = size(cmd_act_mot,2);

gyro_filt = filter(b,a,gyro,get_ic(b,a,gyro(1,:)));
cmd_filt_mot = filter(b,a,cmd_act_mot,get_ic(b,a,cmd_act_mot(1,:)));
cmd_filt_servo = filter(b,a,cmd_act_servo,get_ic(b,a,cmd_act_servo(1,:)));
accel_filt = filter(b,a,accel,get_ic(b,a,accel(1,:)));
% accelned_filt = filter(b,a,accelned,get_ic(b,a,accelned(1,:)));

cmd_filtd_mot = [zeros(1,num_act); diff(cmd_filt_mot,1)]*sf;
cmd_filtdd_mot = [zeros(1,num_act); diff(cmd_filtd_mot,1)]*sf;
cmd_filtd_servo = [zeros(1,num_act); diff(cmd_filt_servo,1)]*sf;
cmd_filtdd_servo = [zeros(1,num_act); diff(cmd_filtd_servo,1)]*sf;
gyro_filtd = [zeros(1,3); diff(gyro_filt,1)]*sf;
gyro_filtdd = [zeros(1,3); diff(gyro_filtd,1)]*sf;
accel_filtd = [zeros(1,3); diff(accel_filt,1)]*sf;
accel_filtdd = [zeros(1,3); diff(accel_filtd,1)]*sf;
% accelned_filtd = [zeros(1,3); diff(accelned_filt,1)]*sf;


return
%% Roll effectiveness

tfit = (1:length(t))/500;

% wn = 2.55*2*pi;
wn = 7*2*pi;
zeta = 0.2;
tf_struct = tf(wn^2, [1 2*zeta*wn wn^2]);
% tf_struct_d = c2d(tf_struct,1/500, 'tustin');

% input_w_structure2 = lsim(tf_struct, cmd_filtd_mot(:,2), tfit);
% input_w_structure4 = lsim(tf_struct, cmd_filtd_mot(:,4), tfit);

% quat_dr = interp1(refquat_t, quat, t, 'nearest'); % Quaternion on the datarange
% [~, phi_dr, theta_dr] = quat2angle(quat_dr,'ZXY');

% input_wo_structure = [ones(size(cmd_filt_mot,1),1) cmd_filt_mot(:,[2,4]) cmd_filt_servo(:,7), theta_filt, gyro_filt(:,1:2)];
input_wo_structure = [cmd_filtd_mot(:,[2,4]) cmd_filtd_servo(:,7) [zeros(1,2); gyro_filt(:,1:2)]];

% input_w_structure = [input_w_structure2 input_w_structure4];

output_roll = gyro_filtdd(:,1);% -0.5772*ones(size(input_w_structure2));
% inputs_roll = [input_wo_structure];
% inputs_roll = [ones(size(input_wo_structure,1),1) input_w_structure, gyro_filt(:,1)];
inputs_roll = [ones(size(input_wo_structure,1),1) input_wo_structure];

% output_roll = gyro_filtd(:,1);
% inputs_roll = [ones(size(gyro_filtd(:,1))) cmd_filt_mot(:,[1 3])];
% inputs_roll = [ones(size(gyro_filtd(:,1))) cmd_filt_mot(:,1)];

Groll = inputs_roll(datarange,:)\output_roll(datarange,:);
figure;
plot(t(datarange),output_roll(datarange,:)); hold on
plot(t(datarange),inputs_roll(datarange,:)*Groll)
title('roll fit')

% figure; plot(input_w_structure2(datarange,:)); hold on; plot(cmd_filt_mot(datarange,2))

ssmodel = ss([-2*zeta*wn -wn^2; 1 0], [wn^2; 0], eye(2), [0;0]);

model = tfest(output_roll(datarange,:), cmd_filt_mot(datarange,[2,4]), ssmodel)

%% Pitch effectiveness
% output_pitch = gyro_filtdd(datarange,2);
% inputs_pitch = [cmd_filtd_mot(datarange,1:4)];

theta_dr = interp1(refquat_t, theta, t, 'nearest','extrap'); % Quaternion on the datarange
theta_filt = filter(b,a,theta_dr);

% output_pitch = gyro_filtd(datarange,2);
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filt_mot(datarange,[1:4, 9]) cmd_filt_servo(datarange,[6,7])];% 0*gyro_filt(datarange,2) theta_filt(datarange)];

output_pitch = gyro_filtdd(datarange,2);
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filt_mot(datarange,[1:4]).*cmd_filt_mot(datarange,[1:4]) cmd_filt_servo(datarange,[6,7]) gyro_filt(datarange,2)];
inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filtd_mot(datarange,[1:4]).*cmd_filt_mot(datarange,[1:4]) cmd_filtd_servo(datarange,[6,7]) gyro_filtd(datarange,2) gyro_filt(datarange,2)];
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filtd_mot(datarange,[1,3]).*cmd_filt_mot(datarange,[1,3]) cmd_filtd_servo(datarange,6) gyro_filtd(datarange,2) gyro_filt(datarange,2)];
% inputs_pitch = [ones(size(gyro_filtd(datarange,2))) cmd_filtd_mot(datarange,[1,3])];% cmd_filtd_servo(datarange,6) 0*gyro_filtd(datarange,2)];

Gpitch = inputs_pitch\output_pitch;
% unit: deg/s^2 per unit pprz_cmd
figure;
plot(t(datarange),output_pitch); hold on
plot(t(datarange),inputs_pitch*Gpitch)
title('pitch fit')

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
