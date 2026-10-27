function plot_settings(ac_data, f)
%PLOT_SETTINGS Values of the settings that changed during the export window, against time from takeoff.
    if nargin < 2 || ~isfield(f.aircraft, 'settings')
        return
    end
    values = f.aircraft.settings.values;
    names = fieldnames(values);
    t0 = -Inf; t1 = Inf;
    if isfield(f.time, 'start_s'), t0 = f.time.start_s; t1 = f.time.end_s; end
    changed = {};
    for i = 1:numel(names)
        v = values.(names{i});
        in = v.timestamp >= t0 & v.timestamp <= t1;
        if numel(unique(v.value(in))) > 1
            changed{end + 1} = names{i}; %#ok<AGROW>
        end
    end
    if isempty(changed)
        text(0.5, 0.5, 'No setting changed in this window', 'HorizontalAlignment', 'center');
        axis off;
        return
    end
    tiledlayout(numel(changed), 1, 'Padding', 'compact', 'TileSpacing', 'compact');
    axs = gobjects(1, numel(changed));
    for i = 1:numel(changed)
        v = values.(changed{i});
        axs(i) = nexttile;
        stairs(v.timestamp - f.time.offset_s, v.value, 'LineWidth', 1.2);
        grid on;
        ylabel(strrep(changed{i}, '_', '\_'), 'Rotation', 0, 'HorizontalAlignment', 'right');
    end
    xlabel(axs(end), 'Time [s]');
    linkaxes(axs, 'x');
    sgtitle('Setting changes');
end
