

trange = [1121 1225];

%%

datarange1 = find(ac_data.IMU_ACCEL_SCALED.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.IMU_ACCEL_SCALED.timestamp>trange(2),1,'first')-1;
acc_range = datarange1:datarange2; % valid for all signals of the same frequency

datarange1 = find(ac_data.ROTORCRAFT_FP.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.ROTORCRAFT_FP.timestamp>trange(2),1,'first')-1;
rot_fp_range = datarange1:datarange2; % valid for all signals of the same frequency

datarange1 = find(ac_data.ROTORCRAFT_CMD.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.ROTORCRAFT_CMD.timestamp>trange(2),1,'first')-1;
rot_cmd_range = datarange1:datarange2; % valid for all signals of the same frequency

datarange1 = find(ac_data.STAB_ATTITUDE.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.STAB_ATTITUDE.timestamp>trange(2),1,'first')-1;
stab_att_range = datarange1:datarange2; % valid for all signals of the same frequency

datarange1 = find(ac_data.INS.timestamp>trange(1),1,'first')-1;
datarange2 = find(ac_data.INS.timestamp>trange(2),1,'first')-1;
ins_range = datarange1:datarange2; % valid for all signals of the same frequency

%% Plots

figure
subplot(3,1,1)
hold off
plot(ac_data.ROTORCRAFT_FP.timestamp(rot_fp_range), ac_data.ROTORCRAFT_FP.carrot_up(rot_fp_range))
hold on
plot(ac_data.ROTORCRAFT_FP.timestamp(rot_fp_range), ac_data.ROTORCRAFT_FP.up(rot_fp_range))
grid on
legend('up_{ref}','up')
xlabel('Time [s]')
ylabel('Height [m]')

subplot(3,1,2)
hold off
plot(ac_data.ROTORCRAFT_CMD.timestamp(rot_cmd_range), ac_data.ROTORCRAFT_CMD.cmd_thrust(rot_cmd_range)./96)
hold on
plot(ac_data.STAB_ATTITUDE.timestamp(stab_att_range), ac_data.STAB_ATTITUDE.theta_ref(stab_att_range)./pi.*180)
plot(ac_data.STAB_ATTITUDE.timestamp(stab_att_range), ac_data.STAB_ATTITUDE.theta(stab_att_range)./pi.*180)
grid on
xlabel('Time [s]')
ylabel('Cmd [% / deg]')
legend('thrust','\theta_{ref}','\theta')



subplot(3,1,3)
hold off
plot(ac_data.INS.timestamp(ins_range), ac_data.INS.ins_zdd_alt(ins_range))
hold on
plot(ac_data.IMU_ACCEL_SCALED.timestamp(acc_range), ac_data.IMU_ACCEL_SCALED.az_alt(acc_range))
grid on
xlabel('Time [s]')
ylabel('acc_z [m/s^2]')
