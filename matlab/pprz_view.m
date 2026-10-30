function fig = pprz_view(logs)
%PPRZ_VIEW Interactive viewer for logbook MATLAB exports.
%   pprz_view               pick .mat / .zip files in a dialog
%   pprz_view(path)         a .mat file, a folder or a bulk .zip
%   pprz_view(logs)         exports loaded with pprz_load
%   Pick a flight, tick the plots (plots whose messages were not logged are marked) and press Plot; the
%   figures open docked as tabs. Uses pprz_plot, so every plot can also be made from a script.
%   Click or drag in any time plot to move a cursor on all of them (arrow keys step it); the table below
%   the buttons shows every plotted value at the cursor (see pprz.Cursor). The Events boxes switch the
%   markers of each kind on or off on the open plots. Compare… overlays the ticked
%   plots of several flights (pprz_compare).
    if nargin < 1
        logs = pprz_load();
    elseif ischar(logs) || isstring(logs)
        logs = pprz_load(logs);
    end
    if isempty(logs)
        fig = [];
        return
    end
    T = pprz_flights(logs);
    T = T(T.has_data, :);
    if isempty(T)
        error('pprz_view:nodata', 'These exports contain no flight data.');
    end
    list = pprz_plots();
    figs = gobjects(0);
    f = [];

    cursor = pprz.Cursor();
    fig = uifigure('Name', 'pprz_view', 'Position', [80 80 380 860]);
    g = uigridlayout(fig, [10 1]);
    g.RowHeight = {'fit', 'fit', 'fit', 'fit', 'fit', '1x', 'fit', 'fit', 'fit', 180};
    uilabel(g, 'Text', 'Flight', 'FontWeight', 'bold');
    rows = arrayfun(@(r) row_label(T(r, :)), 1:height(T), 'UniformOutput', false);
    pick = uidropdown(g, 'Items', rows, 'ItemsData', 1:height(T), 'ValueChangedFcn', @(~, ~) select());
    info = uilabel(g, 'Text', '', 'WordWrap', 'on', 'FontColor', [0.35 0.35 0.35]);

    og = uigridlayout(g, [3 4], 'Padding', [0 0 0 0], 'RowHeight', {'fit', 'fit', 'fit'}, ...
        'ColumnWidth', {'fit', '1x', 'fit', '1x'});
    uilabel(og, 'Text', 'From [s]');
    from = uieditfield(og, 'text', 'Placeholder', 'start', 'Tooltip', 'Time from takeoff; empty: from the start');
    uilabel(og, 'Text', 'To [s]');
    to = uieditfield(og, 'text', 'Placeholder', 'end', 'Tooltip', 'Time from takeoff; empty: to the end');
    uilabel(og, 'Text', 'Shading');
    shade = uidropdown(og, 'Items', {'None', 'Autopilot mode', 'RC mode'}, 'ItemsData', {'none', 'ap', 'rc'});
    shade.Layout.Column = [2 4];
    eg = uigridlayout(og, [1 5], 'Padding', [0 0 0 0], 'ColumnWidth', {'fit', 'fit', 'fit', 'fit', 'fit'});
    eg.Layout.Column = [1 4];
    uilabel(eg, 'Text', 'Events', 'Tooltip', ...
        'Event markers on the plots; ticking applies to the open figures. Click a marker to read its full text');
    kinds = {'mode', 'Modes', true; 'setting', 'Settings', false; 'checklist', 'Checklist', false; ...
        'message', 'Messages', true};
    events = gobjects(1, size(kinds, 1));
    for i = 1:size(kinds, 1)
        events(i) = uicheckbox(eg, 'Text', kinds{i, 2}, 'Value', kinds{i, 3}, ...
            'ValueChangedFcn', @(~, ~) update_markers());
    end

    uilabel(g, 'Text', 'Plots', 'FontWeight', 'bold');
    tree = uitree(g, 'checkbox');
    bg = uigridlayout(g, [2 3], 'Padding', [0 0 0 0], 'ColumnWidth', {'1x', '1x', '1x'});
    uibutton(bg, 'Text', 'Plot', 'ButtonPushedFcn', @(~, ~) draw());
    uibutton(bg, 'Text', 'Compare…', 'ButtonPushedFcn', @(~, ~) compare(), ...
        'Tooltip', 'Overlay the ticked plots of several flights (pprz_compare)');
    uibutton(bg, 'Text', '3-D replay', 'ButtonPushedFcn', @(~, ~) replay(), ...
        'Tooltip', 'Replay the flight with the airframe model (pprz_replay)');
    uibutton(bg, 'Text', 'Save figures…', 'ButtonPushedFcn', @(~, ~) save_figures());
    uibutton(bg, 'Text', 'Close figures', 'ButtonPushedFcn', @(~, ~) close_figures());
    status = uilabel(g, 'Text', '', 'WordWrap', 'on');
    at = uilabel(g, 'Text', 'Cursor: click or drag in a time plot', 'FontWeight', 'bold');
    values = uitable(g, 'ColumnName', {'Plot', 'Signal', 'Value'}, 'RowName', {}, ...
        'ColumnWidth', {'1x', '1x', 70}, 'Data', cell(0, 3));
    cursor.listen(@show_values);
    select();

    function select()
        r = T(pick.Value, :);
        f = pprz_flight(logs, r.log, r.aircraft, r.flight);
        crew = f.aircraft.crew;
        text = sprintf('PIC %s · PAC %s · GCS %s', blank(crew.pic), blank(crew.pac), blank(crew.gcs));
        if isfield(f.summary, 'verdict')
            text = sprintf('%s\n%s · %.0f s · %s', text, f.time.takeoff_utc, f.summary.duration_s, f.summary.verdict);
        end
        info.Text = text;
        checked = {};
        if ~isempty(tree.CheckedNodes)
            checked = {tree.CheckedNodes.NodeData};
        end
        delete(tree.Children);
        groups = unique({list.group}, 'stable');
        for i = 1:numel(groups)
            parent = uitreenode(tree, 'Text', groups{i});
            for j = find(strcmp({list.group}, groups{i}))
                ok = isempty(list(j).needs) || any(cellfun(@(m) pprz.has(f.data, m), list(j).needs));
                label = list(j).title;
                if ~ok
                    label = [label ' (not logged)']; %#ok<AGROW>
                end
                node = uitreenode(parent, 'Text', label, 'NodeData', list(j).name);
                if ok && (any(strcmp(checked, list(j).name)) || isempty(checked) && strcmp(list(j).name, 'overview'))
                    tree.CheckedNodes = [tree.CheckedNodes; node];
                end
            end
            expand(parent);
        end
    end

    function names = checked_plots()
        nodes = tree.CheckedNodes;
        names = {};
        for i = 1:numel(nodes)
            if ischar(nodes(i).NodeData) && ~endsWith(nodes(i).Text, '(not logged)')
                names{end + 1} = nodes(i).NodeData; %#ok<AGROW>
            end
        end
    end

    function range = time_range()
        range = [-Inf Inf];
        if ~isempty(strtrim(from.Value)), range(1) = str2double(from.Value); end
        if ~isempty(strtrim(to.Value)), range(2) = str2double(to.Value); end
    end

    function draw()
        names = checked_plots();
        if isempty(names)
            status.Text = 'Tick one or more plots.';
            return
        end
        range = time_range();
        if any(isnan(range))
            status.Text = 'From and To must be numbers (seconds from takeoff).';
            return
        end
        status.Text = 'Plotting…';
        drawnow;
        made = pprz_plot(f, names{:}, 'modes', shade.Value, 'range', range, 'docked', true, ...
            'events', event_kinds());
        for i = 1:numel(made)
            setappdata(made(i), 'pprz_view_flight', f);  % the flight its markers belong to
        end
        figs = [figs(isgraphics(figs)), made];
        cursor.attach(made);
        status.Text = sprintf('%d figure(s) made.', numel(made));
    end

    function compare()
        names = checked_plots();
        if isempty(names)
            status.Text = 'Tick the plots to compare.';
            return
        end
        d = uifigure('Name', 'Compare flights', 'Position', [fig.Position(1) + 40, fig.Position(2) + 200, 520 380], ...
            'WindowStyle', 'modal');
        dg = uigridlayout(d, [5 2], 'RowHeight', {'fit', '1x', 'fit', 'fit', 'fit'}, 'ColumnWidth', {'fit', '1x'});
        l = uilabel(dg, 'Text', 'Flights (Ctrl/Cmd-click several)', 'FontWeight', 'bold');
        l.Layout.Column = [1 2];
        flights = uilistbox(dg, 'Items', rows, 'ItemsData', 1:height(T), 'Multiselect', 'on', 'Value', pick.Value);
        flights.Layout.Column = [1 2];
        uilabel(dg, 'Text', 'Align at');
        how = uidropdown(dg, 'Items', {'Takeoff', 'First change of setting', 'First switch to mode'}, ...
            'ItemsData', {'takeoff', 'setting', 'mode'});
        uilabel(dg, 'Text', 'Setting / mode');
        what = uieditfield(dg, 'text', 'Placeholder', 'e.g. guidance_h.gain_p or NAV', ...
            'Tooltip', 'Name of the setting (as in the settings list) or autopilot mode');
        ok = uibutton(dg, 'Text', 'Compare', 'ButtonPushedFcn', @(~, ~) run());
        ok.Layout.Column = [1 2];

        function run()
            align = how.Value;
            if ~strcmp(align, 'takeoff')
                if isempty(strtrim(what.Value))
                    uialert(d, 'Give the setting or mode name to align at.', 'Compare flights');
                    return
                end
                align = {align, strtrim(what.Value)};
            end
            sel = T(flights.Value, :);
            chosen = arrayfun(@(k) pprz_flight(logs, sel.log(k), sel.aircraft(k), sel.flight(k)), ...
                1:height(sel), 'UniformOutput', false);
            delete(d);
            status.Text = 'Comparing…';
            drawnow;
            try
                made = pprz_compare(chosen, names{:}, 'align', align, 'range', time_range());
                set(made, 'WindowStyle', 'docked');
                figs = [figs(isgraphics(figs)), made];
                cursor.attach(made);
                status.Text = sprintf('Compared %d flight(s) in %d figure(s).', numel(chosen), numel(made));
            catch err
                status.Text = err.message;
            end
        end
    end

    function k = event_kinds()
        k = kinds([events.Value], 1)';
    end

    function update_markers()
        % Redraw the markers of the plotted figures (not comparisons or replays) for the ticked kinds.
        for fg = figs(isgraphics(figs))
            if isappdata(fg, 'pprz_view_flight')
                pprz.markers(getappdata(fg, 'pprz_view_flight'), fg, event_kinds());
            end
        end
    end

    function replay()
        try
            rf = pprz_replay(f);
            figs = [figs(isgraphics(figs)), rf];
            cursor.listen(getappdata(rf, 'pprz_show'));  % the replay follows the linked cursor
            status.Text = '3-D replay opened; click a plot to jump there.';
        catch err
            status.Text = err.message;
        end
    end

    function save_figures()
        figs = figs(isgraphics(figs));
        if isempty(figs)
            status.Text = 'No figures to save.';
            return
        end
        folder = uigetdir(pwd, 'Save figures as PNG to');
        figure(fig);
        if isequal(folder, 0)
            return
        end
        for i = 1:numel(figs)
            name = regexprep(figs(i).Name, '[^\w\-]+', '_');
            exportgraphics(figs(i), fullfile(folder, [name '.png']), 'Resolution', 150);
        end
        status.Text = sprintf('Saved %d figure(s) to %s.', numel(figs), folder);
    end

    function show_values(t)
        if ~isvalid(fig)
            return
        end
        at.Text = sprintf('Cursor at %.2f s', t);
        v = cursor.values();
        values.Data = [{v.plot}', {v.signal}', num2cell([v.value]')];
    end

    function close_figures()
        close(figs(isgraphics(figs)));
        figs = gobjects(0);
        status.Text = '';
    end
end

function text = row_label(r)
    if r.flight == 0
        text = sprintf('%s · %s · whole log', r.file, r.name);
        return
    end
    utc = char(r.takeoff_utc);
    if numel(utc) >= 16
        utc = utc(12:16);
    end
    text = sprintf('%s · %s · flight %d · %s · %dm%02ds', r.file, r.name, r.index, utc, ...
        floor(r.duration_s / 60), round(mod(r.duration_s, 60)));
end

function s = blank(s)
    if isempty(s)
        s = '-';
    end
end
