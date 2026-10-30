function plot_stab_attitude_indi(ac_data)
%PLOT_STAB_ATTITUDE_INDI Angular accelerations against their references (STAB_ATTITUDE_INDI).
    m = ac_data.STAB_ATTITUDE_INDI;
    ax = pprz.tiles(1, 'Stabilisation INDI');
    colors = lines(3);
    axes_ = 'pqr';
    for c = 1:3
        plot(ax, m.t, m.(['angular_accel_' axes_(c)]), 'Color', colors(c, :), 'DisplayName', ['d' axes_(c)]);
        plot(ax, m.t, m.(['angular_accel_ref_' axes_(c)]), '--', 'Color', colors(c, :), ...
            'DisplayName', ['d' axes_(c) ' reference']);
    end
    ylabel(ax, 'Angular acceleration [rad/s²]');
    legend(ax, 'Location', 'eastoutside');
end
