function plot_map(f, by, basemap)
%PLOT_MAP The flown track on a map, coloured by altitude, speed, autopilot mode or battery voltage, with the
%   flight plan waypoints, the takeoff (circle) and the landing (square).
%   plot_map(f)                         coloured by height
%   plot_map(f, 'speed')                'altitude', 'speed', 'mode' or 'battery'
%   plot_map(f, 'mode', 'streets')      any geobasemap name; default 'satellite' (needs internet)
%   Without GPS or a flight plan origin, or without MATLAB's map axes, it draws local east/north instead.
    if nargin < 2
        by = 'altitude';
    end
    if nargin < 3
        basemap = 'satellite';
    end
    tr = pprz.track(f, by);
    if isempty(tr.t)
        text(0.5, 0.5, 'No position logged', 'HorizontalAlignment', 'center');
        axis off;
        return
    end
    names = tr.names;
    value = tr.value;
    on_map = ~isempty(tr.lat) && exist('geoscatter', 'file') > 0;
    if on_map
        ax = geoaxes();
        geobasemap(ax, basemap);
        geoplot(ax, tr.lat, tr.lon, '-', 'Color', [1 1 1], 'LineWidth', 0.5);
        hold(ax, 'on');
        geoscatter(ax, tr.lat, tr.lon, 12, value, 'filled');
        geoplot(ax, tr.lat(1), tr.lon(1), 'o', tr.lat(end), tr.lon(end), 's', 'MarkerSize', 9, ...
            'MarkerEdgeColor', 'k', 'MarkerFaceColor', 'w', 'LineWidth', 1.5);
        for w = tr.waypoints
            geoplot(ax, w.lat, w.lon, '^', 'MarkerSize', 7, 'MarkerFaceColor', [1 0.85 0], 'MarkerEdgeColor', 'k');
            text(ax, w.lat, w.lon, ['  ' w.name], 'FontSize', 8, 'Color', [1 0.95 0.6], 'Interpreter', 'none');
        end
    else
        ax = gca;
        plot(ax, tr.east, tr.north, '-', 'Color', [0.7 0.7 0.7]);
        hold(ax, 'on');
        scatter(ax, tr.east, tr.north, 12, value, 'filled');
        plot(ax, tr.east(1), tr.north(1), 'ko', tr.east(end), tr.north(end), 'ks', 'MarkerSize', 9, ...
            'MarkerFaceColor', 'w', 'LineWidth', 1.5);
        for w = tr.waypoints
            plot(ax, w.east, w.north, 'k^', 'MarkerSize', 7, 'MarkerFaceColor', [1 0.85 0]);
            text(w.east, w.north, ['  ' w.name], 'Parent', ax, 'FontSize', 8, 'Interpreter', 'none');
        end
        axis(ax, 'equal');
        grid(ax, 'on');
        xlabel(ax, 'East [m]');
        ylabel(ax, 'North [m]');
    end
    cb = colorbar(ax);
    ylabel(cb, tr.label);
    if strcmp(by, 'mode') && ~isempty(names)
        colormap(ax, lines(numel(names)));
        caxis(ax, [0.5, numel(names) + 0.5]);
        try
            set(cb, 'Ticks', 1:numel(names), 'TickLabels', names, 'TickLabelInterpreter', 'none');
        catch  % Octave: the colorbar is an axes
            set(cb, 'YTick', 1:numel(names), 'YTickLabel', names, 'TickLabelInterpreter', 'none');
        end
    else
        colormap(ax, parula(256));
        ok = value(isfinite(value));
        if ~isempty(ok) && max(ok) > min(ok)
            caxis(ax, [min(ok), max(ok)]);
        end
    end
    where = 'GPS';
    if strcmp(tr.source, 'ROTORCRAFT_FP')
        where = 'local position';
    end
    title(ax, sprintf('Track by %s (%s)', lower(strtrim(strtok(tr.label, '['))), where));
end
