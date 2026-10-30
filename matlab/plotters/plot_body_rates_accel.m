function plot_body_rates_accel(ac_data)
%PLOT_BODY_RATES_ACCEL Body rates (pprz.rates: BODY_RATES_ACCEL or the gyro) and body acceleration.
    [t, pqr, source] = pprz.rates(ac_data);
    acc = [];
    if pprz.has(ac_data, 'BODY_RATES_ACCEL')
        a = ac_data.BODY_RATES_ACCEL;
        acc = {a.t, [a.ax, a.ay, a.az]};
    else
        for m = {'IMU_ACCEL', 'IMU_ACCEL_SCALED'}
            if pprz.has(ac_data, m{1})
                a = ac_data.(m{1});
                rows = true(size(a.t));
                if isfield(a, 'id')
                    ids = unique(a.id);
                    rows = a.id == ids(1);
                end
                acc = {a.t(rows), [a.ax(rows), a.ay(rows), a.az(rows)]};
                break
            end
        end
    end
    axs = pprz.tiles(1 + ~isempty(acc), sprintf('Body rates (%s)', source));
    pprz.series(axs(1), t, rad2deg(pqr), {'p', 'q', 'r'});
    ylabel(axs(1), 'Rate [deg/s]');
    if ~isempty(acc)
        pprz.series(axs(2), acc{1}, acc{2}, {'x', 'y', 'z'});
        ylabel(axs(2), 'Acceleration [m/s²]');
    end
end
