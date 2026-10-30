function plot_ground_detect(ac_data)
%PLOT_GROUND_DETECT Ground detection triggers and result over time (GROUND_DETECT).
    m = ac_data.GROUND_DETECT;
    flags = {'Vertical speed', 'Specific thrust', 'Vertical accel', 'AGL', 'HX711'};
    ax = pprz.tiles(1, 'Ground detection');
    bits = double(m.triggers);
    for k = 1:numel(flags)
        stairs(ax, m.t, k + 0.8 * bitget(bits, k), 'LineWidth', 1.2, 'DisplayName', flags{k});
    end
    stairs(ax, m.t, numel(flags) + 1 + 0.8 * double(m.ground_detected ~= 0), 'k', 'LineWidth', 1.5, ...
        'DisplayName', 'Ground detected');
    set(ax, 'YTick', 1:numel(flags) + 1, 'YTickLabel', [flags, {'Ground detected'}], ...
        'YLim', [0.5, numel(flags) + 2]);
end
