function plot_imu_scaled(ac_data, idx)
    % Plot the accelerometer
    ax1 = subplot(3,1,1);
    if isfield(ac_data, 'IMU_ACCEL_SCALED')
        if isfield(ac_data.IMU_ACCEL_SCALED, 'id')
            accel_ids = unique(ac_data.IMU_ACCEL_SCALED.id);

            if exist('idx','var')
                accel_ids = idx;
            end

            for i = 1:length(accel_ids)
                accel_idx = find(ac_data.IMU_ACCEL_SCALED.id == accel_ids(i));
                plot(ac_data.IMU_ACCEL_SCALED.t(accel_idx), [ac_data.IMU_ACCEL_SCALED.ax(accel_idx), ac_data.IMU_ACCEL_SCALED.ay(accel_idx), ac_data.IMU_ACCEL_SCALED.az(accel_idx)]);
                hold on
            end

        else
            plot(ac_data.IMU_ACCEL_SCALED.t, [ac_data.IMU_ACCEL_SCALED.ax, ac_data.IMU_ACCEL_SCALED.ay, ac_data.IMU_ACCEL_SCALED.az]);
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

            if exist('idx','var')
                gyro_ids = idx;
            end

            for i = 1:length(gyro_ids)
                gyro_idx = find(ac_data.IMU_GYRO_SCALED.id == gyro_ids(i));
                plot(ac_data.IMU_GYRO_SCALED.t(gyro_idx), [ac_data.IMU_GYRO_SCALED.gp(gyro_idx), ac_data.IMU_GYRO_SCALED.gq(gyro_idx), ac_data.IMU_GYRO_SCALED.gr(gyro_idx)]);
                hold on
            end

        else
            plot(ac_data.IMU_GYRO_SCALED.t, [ac_data.IMU_GYRO_SCALED.gp, ac_data.IMU_GYRO_SCALED.gq, ac_data.IMU_GYRO_SCALED.gr]);
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

            for i = 1:length(mag_ids)
                mag_idx = find(ac_data.IMU_MAG_SCALED.id == mag_ids(i));
                mag_tot = sqrt(ac_data.IMU_MAG_SCALED.mx(mag_idx).^2 + ac_data.IMU_MAG_SCALED.my(mag_idx).^2 + ac_data.IMU_MAG_SCALED.mz(mag_idx).^2);
                plot(ac_data.IMU_MAG_SCALED.t(mag_idx), [ac_data.IMU_MAG_SCALED.mx(mag_idx), ac_data.IMU_MAG_SCALED.my(mag_idx), ac_data.IMU_MAG_SCALED.mz(mag_idx), mag_tot]);
                hold on
            end

        else
            mag_tot = sqrt(ac_data.IMU_MAG_SCALED.mx.^2 + ac_data.IMU_MAG_SCALED.my.^2 + ac_data.IMU_MAG_SCALED.mz.^2);
            plot(ac_data.IMU_MAG_SCALED.t, [ac_data.IMU_MAG_SCALED.mx, ac_data.IMU_MAG_SCALED.my, ac_data.IMU_MAG_SCALED.mz, mag_tot]);
        end
        title('Magnetometer Scaled');
        xlabel('Time [s]');
        legend('x [Gauss]', 'y [Gauss]', 'z [Gauss]', 'Mag tot [Gauss]');
    end

    sgtitle('Scaled sensors')
    linkaxes([ax1,ax2,ax3],'x')
end