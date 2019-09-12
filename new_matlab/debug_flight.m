clear all
close all

p = parselog('../flight_logs_ship/80_01_09__01_55_10_SD.data');
ac_data = p.aircrafts.data;

% Get the important time areas
ap_in_flight = ac_data.ROTORCRAFT_STATUS.timestamp(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_in_flight))));
ap_motors_on = ac_data.ROTORCRAFT_STATUS.timestamp(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_motors_on))));

% Plot the Rotorcraft Status
figure(1);
subplot(2,1,1);
area(ac_data.ROTORCRAFT_STATUS.timestamp, [ac_data.ROTORCRAFT_STATUS.ap_in_flight, ac_data.ROTORCRAFT_STATUS.ap_motors_on]);
title('Rotorcraft Status');
xlabel('Time [s]');
legend('In flight', 'Motors on');

subplot(2,1,2);
plot(ac_data.ROTORCRAFT_STATUS.timestamp, [ac_data.ROTORCRAFT_STATUS.ap_mode, ac_data.ROTORCRAFT_STATUS.arming_status]);
title('Rotorcraft Status');
xlabel('Time [s]');
legend('Flight mode', 'Arming status');
sgtitle('Status')


% Plot the Rotorcraft FP
if isfield(ac_data, 'ROTORCRAFT_FP')
    figure(2);
    ax1 = subplot(3,1,1);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.psi_alt, ac_data.ROTORCRAFT_FP.carrot_psi_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Heading target');
    xlabel('Time [s]');
    legend('Measured psi [deg]', 'Target psi [deg]');

    ax2 = subplot(3,1,2);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.phi_alt, ac_data.ROTORCRAFT_FP.theta_alt, ac_data.ROTORCRAFT_FP.psi_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Angles');
    xlabel('Time [s]');
    legend('Phi [deg]', 'Theta [deg]', 'Psi [deg]');

    ax3 = subplot(3,1,3);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [sqrt(ac_data.ROTORCRAFT_FP.veast_alt.^2.+ac_data.ROTORCRAFT_FP.vnorth_alt.^2), ac_data.ROTORCRAFT_FP.veast_alt, ac_data.ROTORCRAFT_FP.vnorth_alt, ac_data.ROTORCRAFT_FP.vup_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Speeds');
    xlabel('Speed [s]');
    legend('Ground speed [m/s]', 'Speed east [m/s]', 'Sspeed north [m/s]', 'Speed up [m/s]');
    
    sgtitle('Rotorcraft FP measurements')
    linkaxes([ax1,ax2,ax3],'x')
end


% Plot the sensors 9-axis
if isfield(ac_data, 'IMU_ACCEL_SCALED')
    figure(3);
    ax1 = subplot(4,1,1);
    plot(ac_data.IMU_ACCEL_SCALED.timestamp, [ac_data.IMU_ACCEL_SCALED.ax_alt, ac_data.IMU_ACCEL_SCALED.ay_alt, ac_data.IMU_ACCEL_SCALED.az_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Accelerometer Scaled');
    xlabel('Time [s]');
    legend('x [m/s^2]', 'y [m/s^2]', 'z [m/s^2]');
    
    ax2 = subplot(4,1,2);
    plot(ac_data.IMU_GYRO_SCALED.timestamp, [ac_data.IMU_GYRO_SCALED.gp_alt, ac_data.IMU_GYRO_SCALED.gq_alt, ac_data.IMU_GYRO_SCALED.gr_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Gyrometer Scaled');
    xlabel('Time [s]');
    legend('p [deg/s]', 'q [deg/s]', 'r [deg/s]');
    
    ax3 = subplot(4,1,3);
    mag_tot = sqrt(ac_data.IMU_MAG_SCALED.mx_alt.^2 + ac_data.IMU_MAG_SCALED.my_alt.^2 + ac_data.IMU_MAG_SCALED.mz_alt.^2);
    plot(ac_data.IMU_MAG_SCALED.timestamp, [ac_data.IMU_MAG_SCALED.mx_alt, ac_data.IMU_MAG_SCALED.my_alt, ac_data.IMU_MAG_SCALED.mz_alt, mag_tot]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Magnetometer Scaled');
    xlabel('Time [s]');
    legend('x [Gauss]', 'y [Gauss]', 'z [Gauss]', 'Mag tot [Gauss]');
    
%     ax2 = subplot(4,1,2);
%     resamp_energy =resample(timeseries(ac_data.ENERGY.current, ac_data.ENERGY.timestamp), ac_data.IMU_MAG_RAW.timestamp);
%     mx = (double(ac_data.IMU_MAG_RAW.mx) - resamp_energy.Data .* -1.7139708082316483);
%     my = (double(ac_data.IMU_MAG_RAW.my) - resamp_energy.Data .* -0.7696784114750511);
%     mz = (double(ac_data.IMU_MAG_RAW.mz) - resamp_energy.Data .* 1.1626106815908253);
%     mag_tot = sqrt(mx.^2 + my.^2 + mz.^2);
%     plot(ac_data.IMU_MAG_RAW.timestamp, [mx, my, mz, mag_tot]);
%     hold on;
%     plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
%     title('Magnetometer RAW fixed');
%     xlabel('Time [s]');
%     legend('x []', 'y []', 'z []', 'tot []');
    
%     ax3 = subplot(4,1,3);
%     resamp_energy =resample(timeseries(ac_data.ENERGY.current, ac_data.ENERGY.timestamp), ac_data.IMU_MAG_RAW.timestamp);
%     mx = (double(ac_data.IMU_MAG_RAW.mx));
%     my = (double(ac_data.IMU_MAG_RAW.my));
%     mz = (double(ac_data.IMU_MAG_RAW.mz));
%     mag_tot = sqrt(mx.^2 + my.^2 + mz.^2);
%     plot(ac_data.IMU_MAG_RAW.timestamp, [mx, my, mz, mag_tot]);
%     hold on;
%     plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
%     title('Magnetometer RAW');
%     xlabel('Time [s]');
%     legend('x []', 'y []', 'z []', 'tot []');
    
    ax4 = subplot(4,1,4);
    plot(ac_data.ENERGY.timestamp, [ac_data.ENERGY.current]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Powers');
    xlabel('Times [s]');
    legend('Current [A]');
    
    sgtitle('Scaled sensors')
    linkaxes([ax1,ax2,ax3,ax4],'x')
end

% Plot the hybrid guidance
if isfield(ac_data, 'HYBRID_GUIDANCE')
    figure(5);
    ax1 = subplot(3,1,1);
    plot(ac_data.HYBRID_GUIDANCE.timestamp, [ac_data.HYBRID_GUIDANCE.norm_ref_speed_alt, ac_data.HYBRID_GUIDANCE.speed_sp_x_alt, ac_data.HYBRID_GUIDANCE.speed_sp_y_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Target speeds');
    xlabel('Time [s]');
    legend('Norm ref speed [m/s]', 'Speed x sp [m/s]', 'Speed y sp [m/s]');
    
    ax2 = subplot(3,1,2);
    plot(ac_data.HYBRID_GUIDANCE.timestamp, [ac_data.HYBRID_GUIDANCE.heading_diff, ac_data.HYBRID_GUIDANCE.pos_err_x_alt, ac_data.HYBRID_GUIDANCE.pos_err_y_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Errors');
    xlabel('Time [s]');
    legend('Heading err [deg]', 'X-pos err [m]', 'Y-pos err [m]');
    
    ax3 = subplot(3,1,3);
    plot(ac_data.HYBRID_GUIDANCE.timestamp, [ac_data.HYBRID_GUIDANCE.wind_x_alt, ac_data.HYBRID_GUIDANCE.wind_y_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Wind');
    xlabel('Time [s]');
    legend('x [m/s]', 'y [m/s]');
    
    sgtitle('Hyrbid guidance')
    linkaxes([ax1,ax2,ax3],'x')
end

% Plot the GPS/position status
if isfield(ac_data, 'GPS_INT')
    figure(6);
    ax1 = subplot(3,1,1);
    plot(ac_data.GPS_INT.timestamp, [ac_data.GPS_INT.fix, ac_data.GPS_INT.numsv]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('GPS status');
    xlabel('Time [s]');
    legend('Fix', 'Number of SV');
    
    ax2 = subplot(3,1,2);
    plot(ac_data.GPS_INT.timestamp, [ac_data.GPS_INT.pacc, ac_data.GPS_INT.pdop, ac_data.GPS_INT.sacc]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Errors');
    xlabel('Time [s]');
    legend('Position accuracy', 'PDOP', 'Speed accuracy');
    
    ax3 = subplot(3,1,3);
    plot3(ac_data.GPS_INT.ecef_x, ac_data.GPS_INT.ecef_y, ac_data.GPS_INT.ecef_z);
    %hold on;
    %plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Position (ECEF)');
    %xlabel('Time [s]');
    %legend('x [m]', 'y [m]', 'z [m]');
    
    sgtitle('GPS')
    linkaxes([ax1,ax2],'x')
end

% Plot the EKF2 status
if isfield(ac_data, 'INS_EKF2')
    figure(7);
    ax1 = subplot(2,1,1);
    plot(ac_data.INS_EKF2.timestamp, [ac_data.INS_EKF2.innov_mag, ac_data.INS_EKF2.innov_pos, ac_data.INS_EKF2.innov_vel, ac_data.INS_EKF2.innov_hagl]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('EKF2 innovations');
    xlabel('Time [s]');
    legend('Magnetometer', 'Position ', 'Velocity', 'Height AGL');
    
    ax2 = subplot(2,1,2);
    plot(ac_data.GPS_INT.timestamp, [ac_data.GPS_INT.pacc, ac_data.GPS_INT.pdop, ac_data.GPS_INT.sacc]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Errors');
    xlabel('Time [s]');
    legend('Position accuracy', 'PDOP', 'Speed accuracy');
    
    sgtitle('INS EKF2')
    linkaxes([ax1,ax2],'x')
end

% Plot the Altitude/Height information
if isfield(ac_data, 'ROTORCRAFT_FP')
    figure(8);
    ax1 = subplot(2,1,1);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.carrot_up_alt, ac_data.ROTORCRAFT_FP.up_alt]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Height');
    xlabel('Time [s]');
    legend('Carrot up', 'Up');
    
    ax2 = subplot(2,1,2);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.thrust]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Thurst');
    xlabel('Time [s]');
    legend('Thrust');
    
    sgtitle('Height')
    linkaxes([ax1,ax2],'x')
end

% Plot the Hover loop
if isfield(ac_data, 'HOVER_LOOP')
    figure(9);
    ax1 = subplot(2,1,1);
    x_err = ac_data.HOVER_LOOP.est_x_alt-ac_data.HOVER_LOOP.sp_x_alt;
    y_err = ac_data.HOVER_LOOP.est_y_alt-ac_data.HOVER_LOOP.sp_y_alt;
    plot(ac_data.HOVER_LOOP.timestamp, [x_err, y_err]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Errors');
    xlabel('Time [s]');
    legend('X-err', 'Y-err');
    
    ax2 = subplot(2,1,2);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.thrust]);
    hold on;
    plot([ap_motors_on'; ap_motors_on'], repmat(ylim',1,size(ap_motors_on,1)), '--r')
    title('Thurst');
    xlabel('Time [s]');
    legend('Thrust');
    
    sgtitle('Hover loop')
    linkaxes([ax1,ax2],'x')
end