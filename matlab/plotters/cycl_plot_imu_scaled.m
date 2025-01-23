function cycl_plot_imu_scaled(ac_data, order)

    tiledlayout(2, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    % accel
    ax1 = nexttile;
    hold on; zoom on;
    h1 = plot(ac_data.IMU_ACCEL_SCALED.timestamp, ac_data.IMU_ACCEL_SCALED.ax_alt);
    h2 = plot(ac_data.IMU_ACCEL_SCALED.timestamp, ac_data.IMU_ACCEL_SCALED.ay_alt);
    h3 = plot(ac_data.IMU_ACCEL_SCALED.timestamp, ac_data.IMU_ACCEL_SCALED.az_alt);
    xlabel('Time [s]');
    ylabel('Acceleration [m/s^2]');
    title('Accelerometer Scaled');
    grid on;

    % gyro
    ax2 = nexttile;
    hold on; zoom on;
    h4 = plot(ac_data.IMU_GYRO_SCALED.timestamp, ac_data.IMU_GYRO_SCALED.gp_alt);
    h5 = plot(ac_data.IMU_GYRO_SCALED.timestamp, ac_data.IMU_GYRO_SCALED.gq_alt);
    h6 = plot(ac_data.IMU_GYRO_SCALED.timestamp, ac_data.IMU_GYRO_SCALED.gr_alt);
    xlabel('Time [s]');
    ylabel('Rate [deg/s]');
    title('Gyrometer Scaled');
    grid on;

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1, ax2});
    legend([h1, h2, h3], {'x', 'y', 'z'});
    legend([h4, h5, h6], {'p', 'q', 'r'});

    linkaxes([ax1,ax2],'x');

end