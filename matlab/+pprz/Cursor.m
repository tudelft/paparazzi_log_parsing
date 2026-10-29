classdef Cursor < handle
%CURSOR A time cursor linked across figures: click or drag in any time plot and a vertical line moves to
%   that time on every time plot of every attached figure.
%   c = pprz.Cursor(figs)    attach figures (more later with c.attach(fig))
%   c.move(t)                move the cursor to time t [s]
%   c.listen(@(t) ...)       call a function whenever the cursor moves (a values table, a map, a 3-D view)
%   v = c.values()           every plotted line's value at the cursor: struct array plot, signal, value
%   Keys in an attached figure: left/right arrow step 0.1 s, with shift 1 s. Zoom and pan keep working.

    properties (SetAccess = private)
        time = NaN      % current cursor time [s], NaN until the first click
        figures = gobjects(0)
    end
    properties (Access = private)
        listeners = {}
        dragging = false
    end

    methods
        function c = Cursor(figs)
            if nargin > 0
                c.attach(figs);
            end
        end

        function attach(c, figs)
            for fig = reshape(figs, 1, [])
                if ~isgraphics(fig) || any(c.figures == fig)
                    continue
                end
                c.figures(end + 1) = fig;
                set(fig, 'WindowButtonDownFcn', @(src, ~) c.down(src), ...
                    'WindowButtonMotionFcn', @(src, ~) c.drag(src), ...
                    'WindowButtonUpFcn', @(~, ~) c.release(), ...
                    'WindowKeyPressFcn', @(~, evt) c.key(evt));
                for ax = pprz.time_axes(fig)
                    try  % keep the line full height after zoom or pan
                        addlistener(ax, 'YLim', 'PostSet', @(~, ~) c.draw(fig));
                    catch
                    end
                end
                c.draw(fig);
            end
        end

        function listen(c, fn)
            c.listeners{end + 1} = fn;
        end

        function move(c, t)
            if ~isscalar(t) || ~isfinite(t)
                return
            end
            c.time = t;
            c.figures = c.figures(isgraphics(c.figures));
            for fig = c.figures
                c.draw(fig);
            end
            for k = 1:numel(c.listeners)
                c.listeners{k}(t);
            end
        end

        function v = values(c)
            % The sample at or just before the cursor of each line, NaN outside the line's time span.
            v = struct('plot', {}, 'signal', {}, 'value', {});
            if isnan(c.time)
                return
            end
            for fig = c.figures(isgraphics(c.figures))
                for ax = pprz.time_axes(fig)
                    plot_name = get(fig, 'Name');
                    if ~isempty(axes_name(ax))
                        plot_name = [plot_name ' · ' axes_name(ax)];
                    end
                    lines = findobj(ax, 'Type', 'line', '-not', 'Tag', 'pprz_cursor');
                    for k = numel(lines):-1:1  % findobj lists the newest first
                        ln = lines(k);
                        x = get(ln, 'XData');
                        if numel(x) < 2 || strcmp(get(ln, 'Visible'), 'off')
                            continue
                        end
                        name = char(get(ln, 'DisplayName'));
                        if isempty(name) || startsWith(name, 'data')
                            name = sprintf('line %d', numel(lines) - k + 1);
                        end
                        i = find(x <= c.time, 1, 'last');
                        value = NaN;
                        if ~isempty(i) && c.time <= x(end)
                            y = get(ln, 'YData');
                            value = y(i);
                        end
                        v(end + 1) = struct('plot', plot_name, ...
                            'signal', name, 'value', value); %#ok<AGROW>
                    end
                end
            end
        end
    end

    methods (Access = private)
        function draw(c, fig)
            for ax = pprz.time_axes(fig)
                ln = findall(ax, 'Tag', 'pprz_cursor');
                if isnan(c.time)
                    delete(ln);
                    continue
                end
                y = get(ax, 'YLim');
                if isempty(ln)
                    set(ax, 'YLimMode', 'manual');  % keep the shown limits; zoom and pan still move them
                    line([c.time c.time], y, 'Parent', ax, 'Tag', 'pprz_cursor', 'Color', [0.85 0.15 0.1], ...
                        'LineWidth', 1, 'HitTest', 'off', 'HandleVisibility', 'off');
                else
                    set(ln(1), 'XData', [c.time c.time], 'YData', y);
                end
            end
        end

        function down(c, fig)
            if ~strcmp(get(fig, 'SelectionType'), 'normal')
                return
            end
            ax = ancestor(get(fig, 'CurrentObject'), 'axes');
            if isempty(ax) || ~any(pprz.time_axes(fig) == ax)
                return
            end
            c.dragging = true;
            p = get(ax, 'CurrentPoint');
            c.move(p(1, 1));
        end

        function drag(c, fig)
            ax = get(fig, 'CurrentAxes');
            if c.dragging && ~isempty(ax) && any(pprz.time_axes(fig) == ax)
                p = get(ax, 'CurrentPoint');
                c.move(p(1, 1));
            end
        end

        function release(c)
            c.dragging = false;
        end

        function key(c, evt)
            step = 0.1;
            if any(strcmp(evt.Modifier, 'shift'))
                step = 1;
            end
            if isnan(c.time)
                return
            elseif strcmp(evt.Key, 'rightarrow')
                c.move(c.time + step);
            elseif strcmp(evt.Key, 'leftarrow')
                c.move(c.time - step);
            end
        end
    end
end

function name = axes_name(ax)
    name = strtrim(strjoin(cellstr(get(get(ax, 'Title'), 'String')), ' '));
    if isempty(name)
        name = strtrim(strjoin(cellstr(get(get(ax, 'YLabel'), 'String')), ' '));
    end
end
