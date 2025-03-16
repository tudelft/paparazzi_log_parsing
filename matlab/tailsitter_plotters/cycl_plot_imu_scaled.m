function cycl_plot_imu_scaled(ac_data, filtered, cutoff_freq)

    tiledlayout(2, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    gyro = [ac_data.IMU_GYRO_SCALED.gp_alt ac_data.IMU_GYRO_SCALED.gq_alt ac_data.IMU_GYRO_SCALED.gr_alt];
    accel = [ac_data.IMU_ACCEL_SCALED.ax_alt ac_data.IMU_ACCEL_SCALED.ay_alt ac_data.IMU_ACCEL_SCALED.az_alt];

    if filtered
        [b, a] = butter(2,cutoff_freq/(500/2));
        
        gyro = filter(b,a,gyro,get_ic(b,a,gyro(1,:)));
        accel= filter(b,a,accel,get_ic(b,a,accel(1,:)));
    end

    % accel
    ax1 = nexttile;
    hold on; zoom on;
    h1 = plot(ac_data.IMU_ACCEL_SCALED.timestamp, accel(:,1));
    h2 = plot(ac_data.IMU_ACCEL_SCALED.timestamp, accel(:,2), LineWidth=1.5);
    h3 = plot(ac_data.IMU_ACCEL_SCALED.timestamp, accel(:,3));
    xlabel('Time [s]');
    ylabel('Acceleration [m/s^2]');
    title('Accelerometer Scaled');
    grid on;

    % gyro
    ax2 = nexttile;
    hold on; zoom on;
    h4 = plot(ac_data.IMU_GYRO_SCALED.timestamp, gyro(:,1));
    h5 = plot(ac_data.IMU_GYRO_SCALED.timestamp, gyro(:,2));
    h6 = plot(ac_data.IMU_GYRO_SCALED.timestamp, gyro(:,3));
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