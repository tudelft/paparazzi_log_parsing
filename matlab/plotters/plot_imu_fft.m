function plot_imu_fft(ac_data, idx, bg, nd)    
    
    % Plot the accelerometer
    ax1 = subplot(2,1,1);
    if isfield(ac_data, 'IMU_ACCEL')
        if isfield(ac_data.IMU_ACCEL, 'id')
            accel_ids = unique(ac_data.IMU_ACCEL.id);

            if exist('idx','var')
                accel_ids = idx;
            end

            for i = 1:length(accel_ids)
                if exist('bg','var') && exist('nd','var')
                    accel_idx = find(ac_data.IMU_ACCEL.id == accel_ids(i) & ac_data.IMU_ACCEL.timestamp > bg & ac_data.IMU_ACCEL.timestamp < nd);
                else
                    accel_idx = find(ac_data.IMU_ACCEL.id == accel_ids(i));
                end
                t = ac_data.IMU_ACCEL.timestamp(accel_idx);
                x = ac_data.IMU_ACCEL.ax(accel_idx);
                y = ac_data.IMU_ACCEL.ay(accel_idx);
                z = ac_data.IMU_ACCEL.az(accel_idx);

                dt = mean(diff(t));
                Fs = 1/dt;
                L = length(t);
                f = Fs/L*(0:(L/2));

                xf = fft(x);
                yf = fft(y);
                zf = fft(z);

                hold on; grid on;
                for n = 1:3
                    switch n
                        case 1
                            Y = xf;
                        case 2
                            Y = yf;
                        case 3
                            Y = zf;
                    end
                    P2 = abs(Y/L);
                    P1 = P2(1:L/2+1);
                    P1(2:end-1) = 2*P1(2:end-1);

                    semilogy(f,20*log10(P1))
                    clear P1 P2
                end
            end
        end
        title('Accelerometer');
        xlabel("Frequency [Hz]")
        ylabel("20*log10|P1(f)|")
        legend('x', 'y', 'z');
    end

    % Plot the gyro
    ax2 = subplot(2,1,2);
    if isfield(ac_data, 'IMU_GYRO')
        if isfield(ac_data.IMU_GYRO, 'id')
            gyro_ids = unique(ac_data.IMU_GYRO.id);

            if exist('idx','var')
                gyro_ids = idx;
            end

            for i = 1:length(accel_ids)
                if exist('bg','var') && exist('nd','var')
                    gyro_idx = find(ac_data.IMU_GYRO.id == gyro_ids(i) & ac_data.IMU_GYRO.timestamp > bg & ac_data.IMU_GYRO.timestamp < nd);
                else
                    gyro_idx = find(ac_data.IMU_GYRO.id == gyro_ids(i));
                end
                t = ac_data.IMU_GYRO.timestamp(gyro_idx);
                x = ac_data.IMU_GYRO.gp(gyro_idx);
                y = ac_data.IMU_GYRO.gq(gyro_idx);
                z = ac_data.IMU_GYRO.gr(gyro_idx);

                dt = mean(diff(t));
                Fs = 1/dt;
                L = length(t);
                f = Fs/L*(0:(L/2));

                xf = fft(x);
                yf = fft(y);
                zf = fft(z);

                hold on; grid on;
                for n = 1:3
                    switch n
                        case 1
                            Y = xf;
                        case 2
                            Y = yf;
                        case 3
                            Y = zf;
                    end
                    P2 = abs(Y/L);
                    P1 = P2(1:L/2+1);
                    P1(2:end-1) = 2*P1(2:end-1);

                    semilogy(f,20*log10(P1))
                    clear P1 P2
                end
            end
        end
        title('Gyrometer');
        xlabel("Frequency [Hz]")
        ylabel("20*log10|P1(f)|")
        legend('p', 'q', 'r');
    end

    sgtitle('Periodogram Using FFT')
    linkaxes([ax1,ax2],'x')
    
end