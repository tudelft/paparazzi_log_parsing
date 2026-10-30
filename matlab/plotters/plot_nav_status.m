function plot_nav_status(ac_data, f)
%PLOT_NAV_STATUS Flight plan progress (ROTORCRAFT_NAV_STATUS): block (named from the flight plan) and stage,
%   and the distance to home and to the current waypoint.
    m = ac_data.ROTORCRAFT_NAV_STATUS;
    axs = pprz.tiles(3, 'Navigation');
    block = double(m.cur_block);
    stairs(axs(1), m.t, block, 'LineWidth', 1.2, 'DisplayName', 'Block');
    used = unique(block);
    names = arrayfun(@(b) sprintf('%d', b), used, 'UniformOutput', false);
    try
        all_ = cellstr(f.aircraft.config.flight_plan.blocks);
        ok = used + 1 <= numel(all_);
        names(ok) = all_(used(ok) + 1);
    catch
    end
    set(axs(1), 'YTick', used, 'YTickLabel', names, 'TickLabelInterpreter', 'none', ...
        'YLim', [min(used) - 0.5, max(used) + 0.5]);
    ylabel(axs(1), 'Block');
    stairs(axs(2), m.t, double(m.cur_stage), 'LineWidth', 1.2, 'DisplayName', 'Stage');
    ylabel(axs(2), 'Stage');
    pprz.series(axs(3), m.t, [m.dist_home, m.dist_wp], {'To home', 'To waypoint'});
    ylabel(axs(3), 'Distance [m]');
end
