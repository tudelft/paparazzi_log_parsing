function plot_imu(ac_data, idx)    
    % Plot the accelerometer
    ax1 = subplot(3,1,1);
    if isfield(ac_data, 'IMU_ACCEL')
        if isfield(ac_data.IMU_ACCEL, 'id')
            accel_ids = unique(ac_data.IMU_ACCEL.id);
            
            if exist('idx','var')
                accel_ids = idx;
            end

            for i = 1:length(accel_ids)
                accel_idx = find(ac_data.IMU_ACCEL.id == accel_ids(i));
                plot(ac_data.IMU_ACCEL.timestamp(accel_idx), [ac_data.IMU_ACCEL.ax(accel_idx), ac_data.IMU_ACCEL.ay(accel_idx), ac_data.IMU_ACCEL.az(accel_idx)]);
                hold on
            end

        else
            plot(ac_data.IMU_ACCEL.timestamp, [ac_data.IMU_ACCEL.ax, ac_data.IMU_ACCEL.ay, ac_data.IMU_ACCEL.az]);
        end
        title('Accelerometer Scaled');
        xlabel('Time [s]');
        legend('x [m/s^2]', 'y [m/s^2]', 'z [m/s^2]');
    end
    
    % Plot the gyro
    ax2 = subplot(3,1,2);
    if isfield(ac_data, 'IMU_GYRO')
        if isfield(ac_data.IMU_GYRO, 'id')
            gyro_ids = unique(ac_data.IMU_GYRO.id);

            if exist('idx','var')
                gyro_ids = idx;
            end

            for i = 1:length(gyro_ids)
                gyro_idx = find(ac_data.IMU_GYRO.id == gyro_ids(i));
                plot(ac_data.IMU_GYRO.timestamp(gyro_idx), [ac_data.IMU_GYRO.gp(gyro_idx), ac_data.IMU_GYRO.gq(gyro_idx), ac_data.IMU_GYRO.gr(gyro_idx)]);
                hold on
            end

        else
            plot(ac_data.IMU_GYRO.timestamp, [ac_data.IMU_GYRO.gp, ac_data.IMU_GYRO.gq, ac_data.IMU_GYRO.gr]);
        end
        title('Gyrometer Scaled');
        xlabel('Time [s]');
        legend('p [deg/s]', 'q [deg/s]', 'r [deg/s]');
    end
    
    % Plot the mag
    ax3 = subplot(3,1,3);
    if isfield(ac_data, 'IMU_MAG')
        if isfield(ac_data.IMU_MAG, 'id')
            mag_ids = unique(ac_data.IMU_MAG.id);

            for i = 1:length(mag_ids)
                mag_idx = find(ac_data.IMU_MAG.id == mag_ids(i));
                mag_tot = sqrt(ac_data.IMU_MAG.mx(mag_idx).^2 + ac_data.IMU_MAG.my(mag_idx).^2 + ac_data.IMU_MAG.mz(mag_idx).^2);
                plot(ac_data.IMU_MAG.timestamp(mag_idx), [ac_data.IMU_MAG.mx(mag_idx), ac_data.IMU_MAG.my(mag_idx), ac_data.IMU_MAG.mz(mag_idx), mag_tot]);
                hold on
            end

        else
            mag_tot = sqrt(ac_data.IMU_MAG.mx.^2 + ac_data.IMU_MAG.my.^2 + ac_data.IMU_MAG.mz.^2);
            plot(ac_data.IMU_MAG.timestamp, [ac_data.IMU_MAG.mx, ac_data.IMU_MAG.my, ac_data.IMU_MAG.mz, mag_tot]);
        end
        title('Magnetometer Scaled');
        xlabel('Time [s]');
        legend('x [Gauss]', 'y [Gauss]', 'z [Gauss]', 'Mag tot [Gauss]');
    end

    sgtitle('Scaled sensors')
    linkaxes([ax1,ax2,ax3],'x')
end