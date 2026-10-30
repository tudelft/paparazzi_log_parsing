function plot_map(f, by, basemap)
%PLOT_MAP The flown track on a map, coloured by altitude, speed, autopilot mode or battery voltage, with the
%   flight plan waypoints (moved ones as open triangles), the navigation circles and segments (dashed), the
%   takeoff (green circle) and the landing (square); framed on the flown track.
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
    % Takeoff and landing: t = 0 and the flight duration in a flight export, else the first and last point.
    [~, up] = min(abs(tr.t - 0));
    down = numel(tr.t);
    if isfield(f, 'summary') && isfield(f.summary, 'duration_s') && isfinite(f.summary.duration_s)
        [~, down] = min(abs(tr.t - f.summary.duration_s));
    end
    if ~(tr.t(1) <= 0 && tr.t(end) >= 0)
        up = 1;
    end
    % Frame the flown track (far waypoints stay outside), and label only waypoints in view and not
    % on top of an already labelled one (within 4% of the view).
    span = max([max(tr.east) - min(tr.east), max(tr.north) - min(tr.north), 50]);
    margin = 0.1 * span;
    box = [min(tr.east) - margin, max(tr.east) + margin, min(tr.north) - margin, max(tr.north) + margin];
    shown = false(1, numel(tr.waypoints));
    labelled = zeros(0, 2);
    for k = 1:numel(tr.waypoints)
        w = tr.waypoints(k);
        in = w.east >= box(1) && w.east <= box(2) && w.north >= box(3) && w.north <= box(4);
        near = ~isempty(labelled) && any(hypot(labelled(:, 1) - w.east, labelled(:, 2) - w.north) < 0.04 * span);
        shown(k) = in && ~near;
        if shown(k)
            labelled(end + 1, :) = [w.east, w.north]; %#ok<AGROW>
        end
    end
    on_map = ~isempty(tr.lat) && exist('geoscatter', 'file') > 0;
    if on_map
        ax = geoaxes();
        geobasemap(ax, basemap);
        geoplot(ax, tr.lat, tr.lon, '-', 'Color', [1 1 1], 'LineWidth', 0.5);
        hold(ax, 'on');
        geoscatter(ax, tr.lat, tr.lon, 12, value, 'filled');
        label_color = [0.1 0.1 0.1];  % dark on streets/topographic maps, light on satellite images
        if any(strcmpi(basemap, {'satellite', 'darkwater', 'colorterrain'}))
            label_color = [1 0.95 0.6];
        end
        for sh = reshape(tr.shapes, 1, [])
            if strcmp(sh.kind, 'moved')
                geoplot(ax, sh.lat, sh.lon, '^', 'MarkerSize', 7, 'MarkerEdgeColor', [1 0.85 0], 'LineWidth', 1.2);
            else
                geoplot(ax, sh.lat, sh.lon, '--', 'Color', [1 1 1 0.7], 'LineWidth', 1);
            end
        end
        for k = 1:numel(tr.waypoints)
            w = tr.waypoints(k);
            geoplot(ax, w.lat, w.lon, '^', 'MarkerSize', 7, 'MarkerFaceColor', [1 0.85 0], 'MarkerEdgeColor', 'k');
            if shown(k)
                text(ax, w.lat, w.lon, ['  ' w.name], 'FontSize', 8, 'Color', label_color, 'Interpreter', 'none');
            end
        end
        geoplot(ax, tr.lat(down), tr.lon(down), 's', 'MarkerSize', 9, 'MarkerEdgeColor', 'k', ...
            'MarkerFaceColor', 'w', 'LineWidth', 1.5);
        geoplot(ax, tr.lat(up), tr.lon(up), 'o', 'MarkerSize', 11, 'MarkerEdgeColor', 'k', ...
            'MarkerFaceColor', [0.3 0.85 0.3], 'LineWidth', 1.5);  % on top: visible when it lands where it took off
        geolimits(ax, tr.origin(1) + box(3:4) / 110574, tr.origin(2) + box(1:2) / (cosd(tr.origin(1)) * 111320));
    else
        ax = gca;
        plot(ax, tr.east, tr.north, '-', 'Color', [0.7 0.7 0.7]);
        hold(ax, 'on');
        scatter(ax, tr.east, tr.north, 12, value, 'filled');
        for sh = reshape(tr.shapes, 1, [])
            if strcmp(sh.kind, 'moved')
                plot(ax, sh.east, sh.north, '^', 'MarkerSize', 7, 'MarkerEdgeColor', [0.8 0.6 0], 'LineWidth', 1.2);
            else
                plot(ax, sh.east, sh.north, '--', 'Color', [0.5 0.5 0.5]);
            end
        end
        for k = 1:numel(tr.waypoints)
            w = tr.waypoints(k);
            plot(ax, w.east, w.north, 'k^', 'MarkerSize', 7, 'MarkerFaceColor', [1 0.85 0]);
            if shown(k)
                text(w.east, w.north, ['  ' w.name], 'Parent', ax, 'FontSize', 8, 'Interpreter', 'none');
            end
        end
        plot(ax, tr.east(down), tr.north(down), 'ks', 'MarkerSize', 9, 'MarkerFaceColor', 'w', 'LineWidth', 1.5);
        plot(ax, tr.east(up), tr.north(up), 'ko', 'MarkerSize', 11, 'MarkerFaceColor', [0.3 0.85 0.3], 'LineWidth', 1.5);
        axis(ax, 'equal');
        axis(ax, box);
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
