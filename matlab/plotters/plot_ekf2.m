function plot_ekf2(ac_data)
%PLOT_EKF2 EKF2 innovation test ratios, and from INS_EKF2_EXT the vibration metrics and GPS drift.
    e = ac_data.INS_EKF2;
    ext = pprz.has(ac_data, 'INS_EKF2_EXT');
    axs = pprz.tiles(1 + 2 * ext, 'INS EKF2');
    fields = {'innov_mag', 'Magnetometer'; 'innov_vel', 'Velocity'; 'innov_pos', 'Position'; ...
        'innov_hgt', 'Height'; 'innov_tas', 'Airspeed'; 'innov_hagl', 'Height AGL'; 'innov_beta', 'Sideslip'; ...
        'innov_flow', 'Optical flow'};
    for k = 1:size(fields, 1)
        if ~isfield(e, fields{k, 1})
            continue
        end
        v = double(e.(fields{k, 1}));
        if any(v ~= 0) && max(abs(v)) < 1e6  % unused ones can hold garbage
            plot(axs(1), e.t, double(e.(fields{k, 1})), 'DisplayName', fields{k, 2});
        end
    end
    ylabel(axs(1), 'Innovation test ratio');
    legend(axs(1), 'Location', 'eastoutside');
    if ext
        x = ac_data.INS_EKF2_EXT;
        pprz.series(axs(2), x.t, [x.accel_vibe_hf, x.gyro_vibe_hf, x.gyro_vibe_coning], ...
            {'Accel high freq.', 'Gyro high freq.', 'Gyro coning'});
        ylabel(axs(2), 'Vibration metric');
        pprz.series(axs(3), x.t, [x.gps_drift_h, x.gps_drift_h_filt, x.gps_drift_v], ...
            {'Horizontal', 'Horizontal filtered', 'Vertical'});
        ylabel(axs(3), 'GPS drift [m/s]');
    end
end
