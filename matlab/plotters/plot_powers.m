function plot_powers(ac_data)
%PLOT_POWERS Power per POWER_DEVICE circuit (node.circuit), voltage × current.
    m = ac_data.POWER_DEVICE;
    id = double(m.node_id) + double(m.circuit) / 100;
    ax = pprz.tiles(1, 'Power devices');
    for p = reshape(unique(id), 1, [])
        rows = id == p;
        plot(ax, m.t(rows), double(m.current(rows)) .* double(m.voltage(rows)), ...
            'DisplayName', sprintf('Node %d circuit %d', floor(p), round(100 * (p - floor(p)))));
    end
    ylabel(ax, 'Power [W]');
    legend(ax, 'Location', 'eastoutside');
end
