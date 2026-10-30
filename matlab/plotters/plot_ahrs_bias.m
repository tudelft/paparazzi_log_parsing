function plot_ahrs_bias(ac_data)
%PLOT_AHRS_BIAS Sensor bias estimates of the AHRS (AHRS_BIAS): accelerometer, gyro and magnetometer.
    m = ac_data.AHRS_BIAS;
    axs = pprz.tiles(3, 'AHRS bias');
    pprz.series(axs(1), m.t, [m.accel_x, m.accel_y, m.accel_z], {'x', 'y', 'z'});
    ylabel(axs(1), 'Accel [m/s²]');
    pprz.series(axs(2), m.t, rad2deg([m.gyro_p, m.gyro_q, m.gyro_r]), {'p', 'q', 'r'});
    ylabel(axs(2), 'Gyro [deg/s]');
    pprz.series(axs(3), m.t, [m.mag_x, m.mag_y, m.mag_z], {'x', 'y', 'z'});
    ylabel(axs(3), 'Mag [mGauss]');
end
