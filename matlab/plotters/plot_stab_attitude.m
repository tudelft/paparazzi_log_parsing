function plot_stab_attitude(ac_data)
%PLOT_STAB_ATTITUDE Attitude, rates and angular accelerations against their references (STAB_ATTITUDE, sent
%   in rad, rad/s and rad/s²; shown in degrees).
    m = ac_data.STAB_ATTITUDE;
    rows = {'att', 'att_ref', 'Attitude [deg]', {'Phi', 'Theta', 'Psi'}; ...
        'angular_rate', 'angular_rate_ref', 'Rate [deg/s]', {'p', 'q', 'r'}; ...
        'angular_accel', 'angular_accel_ref', 'Angular acceleration [deg/s²]', {'dp', 'dq', 'dr'}};
    axs = pprz.tiles(3, 'Stabilisation attitude');
    colors = lines(3);
    for k = 1:3
        x = rad2deg(double(m.(rows{k, 1})));
        r = rad2deg(double(m.(rows{k, 2})));
        if k == 1 && size(x, 2) >= 3 && size(r, 2) >= 3
            x(:, 3) = rad2deg(unwrap(deg2rad(x(:, 3))));
            r(:, 3) = rad2deg(unwrap(deg2rad(r(:, 3))));
        end
        for c = 1:min(3, size(x, 2))
            plot(axs(k), m.t, x(:, c), 'Color', colors(c, :), 'DisplayName', rows{k, 4}{c});
            plot(axs(k), m.t, r(:, c), '--', 'Color', colors(c, :), 'DisplayName', [rows{k, 4}{c} ' reference']);
        end
        ylabel(axs(k), rows{k, 3});
        legend(axs(k), 'Location', 'eastoutside');
    end
end
