function plot_guidance(ac_data)
%PLOT_GUIDANCE Position, velocity and acceleration against their references, north, east and down (GUIDANCE).
    m = ac_data.GUIDANCE;
    axs = pprz.tiles(3, 'Guidance');
    colors = lines(3);
    rows = {'pos', 'Position [m]'; 'vel', 'Velocity [m/s]'; 'acc', 'Acceleration [m/s²]'};
    axes_ = {'N', 'North'; 'E', 'East'; 'D', 'Down'};
    for k = 1:3
        for c = 1:3
            name = sprintf('%s_%s', rows{k, 1}, axes_{c, 1});
            plot(axs(k), m.t, m.(name), 'Color', colors(c, :), 'DisplayName', axes_{c, 2});
            plot(axs(k), m.t, m.([name '_ref']), '--', 'Color', colors(c, :), 'DisplayName', [axes_{c, 2} ' reference']);
        end
        ylabel(axs(k), rows{k, 2});
        legend(axs(k), 'Location', 'eastoutside');
    end
end
