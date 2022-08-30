function plot_imu(ac_data)    
    % Plot the accelerometer
    ax1 = subplot(3,1,1);
    if isfield(ac_data, 'IMU_ACCEL_SCALED')
        if isfield(ac_data.IMU_ACCEL_SCALED, 'id')
            accel_ids = unique(ac_data.IMU_ACCEL_SCALED.id);

            for i = 1:length(accel_ids)
                accel_idx = find(ac_data.IMU_ACCEL_SCALED.id == accel_ids(i));
                plot(ac_data.IMU_ACCEL_SCALED.timestamp(accel_idx), [ac_data.IMU_ACCEL_SCALED.ax_alt(accel_idx), ac_data.IMU_ACCEL_SCALED.ay_alt(accel_idx), ac_data.IMU_ACCEL_SCALED.az_alt(accel_idx)]);
                hold on
            end

        else
            plot(ac_data.IMU_ACCEL_SCALED.timestamp, [ac_data.IMU_ACCEL_SCALED.ax_alt, ac_data.IMU_ACCEL_SCALED.ay_alt, ac_data.IMU_ACCEL_SCALED.az_alt]);
        end
        title('Accelerometer Scaled');
        xlabel('Time [s]');
        legend('x [m/s^2]', 'y [m/s^2]', 'z [m/s^2]');
    end
    
    % Plot the gyro
    ax2 = subplot(3,1,2);
    if isfield(ac_data, 'IMU_GYRO_SCALED')
        if isfield(ac_data.IMU_GYRO_SCALED, 'id')
            gyro_ids = unique(ac_data.IMU_GYRO_SCALED.id);

            for i = 1:length(accel_ids)
                gyro_idx = find(ac_data.IMU_GYRO_SCALED.id == gyro_ids(i));
                plot(ac_data.IMU_GYRO_SCALED.timestamp(gyro_idx), [ac_data.IMU_GYRO_SCALED.gp_alt(gyro_idx), ac_data.IMU_GYRO_SCALED.gq_alt(gyro_idx), ac_data.IMU_GYRO_SCALED.gr_alt(gyro_idx)]);
                hold on
            end

        else
            plot(ac_data.IMU_GYRO_SCALED.timestamp, [ac_data.IMU_GYRO_SCALED.gp_alt, ac_data.IMU_GYRO_SCALED.gq_alt, ac_data.IMU_GYRO_SCALED.gr_alt]);
        end
        title('Gyrometer Scaled');
        xlabel('Time [s]');
        legend('p [deg/s]', 'q [deg/s]', 'r [deg/s]');
    end
    
    % Plot the mag
    ax3 = subplot(3,1,3);
    if isfield(ac_data, 'IMU_MAG_SCALED')
        if isfield(ac_data.IMU_MAG_SCALED, 'id')
            mag_ids = unique(ac_data.IMU_MAG_SCALED.id);

            for i = 1:length(accel_ids)
                mag_idx = find(ac_data.IMU_MAG_SCALED.id == mag_ids(i));
                mag_tot = sqrt(ac_data.IMU_MAG_SCALED.mx_alt(mag_idx).^2 + ac_data.IMU_MAG_SCALED.my_alt(mag_idx).^2 + ac_data.IMU_MAG_SCALED.mz_alt(mag_idx).^2);
                plot(ac_data.IMU_MAG_SCALED.timestamp(mag_idx), [ac_data.IMU_MAG_SCALED.mx_alt(mag_idx), ac_data.IMU_MAG_SCALED.my_alt(mag_idx), ac_data.IMU_MAG_SCALED.mz_alt(mag_idx), mag_tot]);
                hold on
            end

        else
            mag_tot = sqrt(ac_data.IMU_MAG_SCALED.mx_alt.^2 + ac_data.IMU_MAG_SCALED.my_alt.^2 + ac_data.IMU_MAG_SCALED.mz_alt.^2);
            plot(ac_data.IMU_MAG_SCALED.timestamp, [ac_data.IMU_MAG_SCALED.mx_alt, ac_data.IMU_MAG_SCALED.my_alt, ac_data.IMU_MAG_SCALED.mz_alt, mag_tot]);
        end
        title('Magnetometer Scaled');
        xlabel('Time [s]');
        legend('x [Gauss]', 'y [Gauss]', 'z [Gauss]', 'Mag tot [Gauss]');
    end

    sgtitle('Scaled sensors')
    linkaxes([ax1,ax2,ax3],'x')
end