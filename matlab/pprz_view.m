function fig = pprz_view(logs)
%PPRZ_VIEW Interactive viewer for logbook MATLAB exports, in one window.
%   pprz_view               pick .mat / .zip files in a dialog
%   pprz_view(path)         a .mat file, a folder or a bulk .zip
%   pprz_view(logs)         exports loaded with pprz_load
%   Left: pick a flight, the time window (applied live to every plot), shading and event markers, tick plots
%   (search; unlogged plots are greyed out) and press Plot. Plots open as tabs on the right; Undock moves one
%   to its own figure. Click or drag in any time plot to move the cursor on all of them (arrow keys step it);
%   below the plots are the values at the cursor and the events near it (click one to jump there).
%   Analysis: 3-D replay (follows the cursor), system identification and control effectiveness.
%   Compare flights… overlays the ticked plots of several flights (pprz_compare).
%   Every plot can also be made from a script with pprz_plot. getappdata(fig, 'pprz_view') holds functions
%   to drive the window from code: plot(names), select(row), close_all().
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
    f = [];
    span = [0 1];
    nearby = struct('t', {}, 'kind', {}, 'label', {}, 'text', {});
    kinds = {'mode', 'Modes', true; 'setting', 'Settings', false; 'checklist', 'Checklist', false; ...
        'message', 'Messages', true};

    cursor = pprz.Cursor();
    fig = uifigure('Name', 'pprz_view', 'Position', [60 60 1320 840]);
    main = uigridlayout(fig, [1 2], 'ColumnWidth', {310, '1x'}, 'Padding', [0 0 0 0], 'ColumnSpacing', 0);

    % ---- left: controls
    side = uigridlayout(main, [16 1], 'RowSpacing', 4, 'Scrollable', 'on', 'RowHeight', ...
        {'fit', 'fit', 'fit', 'fit', 'fit', 'fit', 'fit', 'fit', 'fit', 'fit', 'fit', '1x', 'fit', 'fit', 'fit', 'fit'});
    heading(side, 'Flight');
    rows = arrayfun(@(r) row_label(T(r, :)), 1:height(T), 'UniformOutput', false);
    pick = uidropdown(side, 'Items', rows, 'ItemsData', 1:height(T), 'ValueChangedFcn', @(~, ~) select());
    info = uilabel(side, 'Text', '', 'WordWrap', 'on', 'FontColor', [0.4 0.4 0.4], 'FontSize', 11);

    window_label = heading(side, 'Time window');
    try  % range slider (R2023b and later)
        window = uislider(side, 'range', 'ValueChangedFcn', @(~, ~) apply_window(), ...
            'ValueChangingFcn', @(~, evt) show_window(evt.Value));
        window_fields = [];
    catch
        window = [];
        wg = uigridlayout(side, [1 4], 'Padding', [0 0 0 0], 'ColumnWidth', {'fit', '1x', 'fit', '1x'});
        uilabel(wg, 'Text', 'From');
        window_fields = uieditfield(wg, 'numeric', 'ValueChangedFcn', @(~, ~) apply_window());
        uilabel(wg, 'Text', 'to');
        window_fields(2) = uieditfield(wg, 'numeric', 'ValueChangedFcn', @(~, ~) apply_window());
    end

    heading(side, 'Display');
    dg = uigridlayout(side, [3 3], 'Padding', [0 0 0 0], 'RowSpacing', 2, 'ColumnWidth', {58, 'fit', 'fit'}, ...
        'RowHeight', {'fit', 'fit', 'fit'});
    uilabel(dg, 'Text', 'Shading', 'FontColor', [0.4 0.4 0.4]);
    shade = uidropdown(dg, 'Items', {'None', 'Autopilot mode', 'RC mode'}, 'ItemsData', {'none', 'ap', 'rc'}, ...
        'Tooltip', 'Shade mode regions in new plots');
    shade.Layout.Column = [2 3];
    events = gobjects(1, size(kinds, 1));
    for i = 1:size(kinds, 1)
        if i == 1 || i == 3
            uilabel(dg, 'Text', ifelse(i == 1, 'Events', ''), 'FontColor', [0.4 0.4 0.4], 'Tooltip', ...
                'Event markers on the plots; click a marker to read its full text');
        end
        events(i) = uicheckbox(dg, 'Text', kinds{i, 2}, 'Value', kinds{i, 3}, ...
            'ValueChangedFcn', @(~, ~) update_markers());
    end

    plots_label = heading(side, 'Plots');
    sg = uigridlayout(side, [1 2], 'Padding', [0 0 0 0], 'ColumnWidth', {'1x', 'fit'});
    search = uieditfield(sg, 'text', 'Placeholder', 'Search plots…', 'ValueChangingFcn', @(~, evt) fill_tree(evt.Value));
    logged_only = uicheckbox(sg, 'Text', 'Logged only', 'Value', false, 'ValueChangedFcn', @(~, ~) fill_tree());
    tree = uitree(side, 'checkbox', 'CheckedNodesChangedFcn', @(~, ~) count_ticked());
    pg = uigridlayout(side, [1 2], 'Padding', [0 0 0 0], 'ColumnWidth', {'1x', '1x'});
    plot_button = uibutton(pg, 'Text', 'Plot', 'FontWeight', 'bold', 'ButtonPushedFcn', @(~, ~) draw());
    uibutton(pg, 'Text', 'Compare flights…', 'ButtonPushedFcn', @(~, ~) compare(), ...
        'Tooltip', 'Overlay the ticked plots of several flights (pprz_compare)');

    heading(side, 'Analysis');
    ag = uigridlayout(side, [1 3], 'Padding', [0 0 0 0], 'ColumnWidth', {'1x', '1x', '1x'});
    uibutton(ag, 'Text', '3-D replay', 'ButtonPushedFcn', @(~, ~) replay(), ...
        'Tooltip', 'Replay the flight with the airframe model; follows the cursor (pprz_replay)');
    uibutton(ag, 'Text', 'System ID', 'ButtonPushedFcn', @(~, ~) sysid(), ...
        'Tooltip', 'Doublets, chirps, setting and RC stick steps (pprz_sysid)');
    uibutton(ag, 'Text', 'Effectiveness', 'ButtonPushedFcn', @(~, ~) effectiveness(), ...
        'Tooltip', 'Control effectiveness in the time window (pprz_effectiveness)');
    status = uilabel(side, 'Text', '', 'WordWrap', 'on', 'FontColor', [0.4 0.4 0.4], 'FontSize', 11);

    % ---- right: plot tabs, cursor values and nearby events
    right = uigridlayout(main, [3 1], 'RowHeight', {'fit', '1x', 170}, 'RowSpacing', 4);
    tg = uigridlayout(right, [1 5], 'Padding', [0 0 0 0], 'ColumnWidth', {'1x', 'fit', 'fit', 'fit', 'fit'});
    open_label = uilabel(tg, 'Text', 'No plots open', 'FontColor', [0.4 0.4 0.4]);
    uibutton(tg, 'Text', 'Undock', 'Tooltip', 'Move this plot to its own figure', 'ButtonPushedFcn', @(~, ~) undock());
    uibutton(tg, 'Text', 'Save PNG…', 'ButtonPushedFcn', @(~, ~) save_png());
    uibutton(tg, 'Text', 'Close', 'ButtonPushedFcn', @(~, ~) close_tab(tabs.SelectedTab));
    uibutton(tg, 'Text', 'Close all', 'ButtonPushedFcn', @(~, ~) close_all());
    tabs = uitabgroup(right);
    bottom = uigridlayout(right, [1 2], 'Padding', [0 0 0 0], 'ColumnWidth', {'1x', 320});
    cg = uigridlayout(bottom, [2 4], 'Padding', [4 4 4 4], 'RowHeight', {'fit', '1x'}, ...
        'ColumnWidth', {'1x', 160, 'fit', 'fit'}, 'BackgroundColor', [1 1 1]);
    at = uilabel(cg, 'Text', 'Cursor: click or drag in a time plot', 'FontWeight', 'bold');
    signal_filter = uieditfield(cg, 'text', 'Placeholder', 'Filter signals…', ...
        'ValueChangingFcn', @(~, evt) show_values(cursor.time, evt.Value));
    uibutton(cg, 'Text', '◀', 'Tooltip', 'Step back 0.1 s (arrow keys in a plot; shift: 1 s)', ...
        'ButtonPushedFcn', @(~, ~) cursor.move(cursor.time - 0.1));
    uibutton(cg, 'Text', '▶', 'Tooltip', 'Step forward 0.1 s', 'ButtonPushedFcn', @(~, ~) cursor.move(cursor.time + 0.1));
    values = uitable(cg, 'ColumnName', {'Plot', 'Signal', 'Value'}, 'RowName', {}, ...
        'ColumnWidth', {'1x', '1x', 80}, 'Data', cell(0, 3));
    values.Layout.Column = [1 4];
    eg = uigridlayout(bottom, [2 1], 'Padding', [4 4 4 4], 'RowHeight', {'fit', '1x'}, 'BackgroundColor', [1 1 1]);
    uilabel(eg, 'Text', 'Events near the cursor', 'FontWeight', 'bold');
    near = uilistbox(eg, 'Items', {}, 'ValueChangedFcn', @(~, ~) jump_to_event(), ...
        'Tooltip', 'Click an event to move the cursor there');

    cursor.attach(fig);
    cursor.listen(@(t) show_values(t));
    setappdata(fig, 'pprz_view', struct('plot', @(names) draw(cellstr(names)), ...
        'select', @(row) select_row(row), 'close_all', @() close_all()));
    select();

    % ---- flight and plot list
    function select_row(row)
        pick.Value = row;
        select();
    end

    function select()
        r = T(pick.Value, :);
        f = pprz_flight(logs, r.log, r.aircraft, r.flight);
        crew = f.aircraft.crew;
        text = sprintf('PIC %s · PAC %s · GCS %s', blank(crew.pic), blank(crew.pac), blank(crew.gcs));
        if isfield(f.summary, 'verdict')
            text = sprintf('%s · %s', text, f.summary.verdict);
        end
        info.Text = sprintf('%s\n%d flights in %d export(s)', text, height(T), numel(logs));
        span = data_span(f);
        if ~isempty(window)
            window.Limits = span;
            window.Value = span;
            ticks = unique(round([span(1), 0, linspace(span(1), span(2), 4), span(2)]));
            window.MajorTicks = ticks(ticks >= span(1) & ticks <= span(2));
        else
            set(window_fields, {'Value'}, num2cell(span(:)));
        end
        show_window(span);
        fill_tree();
        status.Text = '';
    end

    function fill_tree(query)
        if nargin < 1
            query = search.Value;
        end
        checked = {};
        if ~isempty(tree.CheckedNodes)
            checked = {tree.CheckedNodes.NodeData};
        elseif isempty(tree.Children)
            checked = {'overview'};
        end
        delete(tree.Children);
        words = lower(strtrim(query));
        logged = arrayfun(@(p) isempty(p.needs) || any(cellfun(@(m) pprz.has(f.data, m), p.needs)), list);
        grey = [];
        try
            grey = uistyle('FontColor', [0.62 0.62 0.62], 'FontAngle', 'italic');
        catch
        end
        groups = unique({list.group}, 'stable');
        for i = 1:numel(groups)
            members = find(strcmp({list.group}, groups{i}));
            shown = members(arrayfun(@(j) (logged(j) || ~logged_only.Value) && (isempty(words) || ...
                contains(lower([list(j).title ' ' list(j).name ' ' list(j).group]), words)), members));
            if isempty(shown)
                continue
            end
            parent = uitreenode(tree, 'Text', sprintf('%s  (%d)', groups{i}, nnz(logged(shown))));
            for j = shown
                label = list(j).title;
                if ~logged(j)
                    label = [label ' · not logged']; %#ok<AGROW>
                end
                node = uitreenode(parent, 'Text', label, 'NodeData', list(j).name);
                if ~logged(j) && ~isempty(grey)
                    try
                        addStyle(tree, grey, 'node', node);
                    catch
                    end
                end
                if logged(j) && any(strcmp(checked, list(j).name))
                    tree.CheckedNodes = [tree.CheckedNodes; node];
                end
            end
            if ~isempty(words) || any(arrayfun(@(j) any(strcmp(checked, list(j).name)), shown))
                expand(parent);
            end
        end
        count_ticked();
    end

    function names = checked_plots()
        names = {};
        for node = reshape(tree.CheckedNodes, 1, [])
            j = find(strcmp({list.name}, node.NodeData), 1);
            if ~isempty(j) && (isempty(list(j).needs) || any(cellfun(@(m) pprz.has(f.data, m), list(j).needs)))
                names{end + 1} = node.NodeData; %#ok<AGROW>
            end
        end
    end

    function count_ticked()
        n = numel(checked_plots());
        plot_button.Text = sprintf('▶ Plot %d', n);
        plot_button.Enable = n > 0;
        plots_label.Text = sprintf('PLOTS · %d TICKED', n);
    end

    % ---- time window: applied to every time axis, no replotting
    function range = time_window()
        if ~isempty(window)
            range = window.Value;
        else
            range = [window_fields.Value];
        end
        if ~(range(2) > range(1))
            range = span;
        end
    end

    function show_window(range)
        window_label.Text = sprintf('TIME WINDOW · %.0f … %.0f s', range(1), range(2));
    end

    function apply_window()
        range = time_window();
        show_window(range);
        axs = pprz.time_axes(tabs);
        if ~isempty(axs)
            set(axs, 'XLim', range);
        end
    end

    % ---- plots as tabs
    function draw(names)
        if nargin < 1
            names = checked_plots();
        end
        if isempty(names)
            status.Text = 'Tick one or more plots.';
            return
        end
        status.Text = 'Plotting…';
        drawnow;
        made = pprz_plot(f, names{:}, 'modes', shade.Value, 'visible', false);
        for k = 1:numel(made)
            tab = adopt(made(k), f);
            pprz.markers(f, tab, event_kinds());
        end
        apply_window();
        cursor.attach(fig);
        status.Text = sprintf('%d plot(s) added.', numel(made));
        if numel(made) < numel(names)
            status.Text = [status.Text ' Some plots failed: see the MATLAB command window.'];
        end
    end

    function tab = adopt(src, flight)
        % Move a figure's contents into a new tab (named after the figure, without the flight title).
        name = regexprep(get(src, 'Name'), ' - .*$', '');
        tab = uitab(tabs, 'Title', name, 'BackgroundColor', [1 1 1], 'Tooltip', get(src, 'Name'));
        setappdata(tab, 'pprz_view_flight', flight);
        delete(findall(src, 'Type', 'subplottext'));  % sgtitle stays with its figure; the tab names the plot
        kids = allchild(src);
        keep = ~ismember(get(kids, {'Type'}), {'uimenu', 'uitoolbar', 'uicontextmenu'});
        set(kids(keep), 'Parent', tab);
        delete(src);
        tabs.SelectedTab = tab;
        refresh_tabs();
    end

    function refresh_tabs()
        n = numel(tabs.Children);
        if n == 0
            open_label.Text = 'No plots open';
        else
            open_label.Text = sprintf('%d plot(s) open · %s', n, f.title);
        end
    end

    function undock()
        tab = tabs.SelectedTab;
        if isempty(tab)
            return
        end
        out = figure('Name', char(tab.Tooltip), 'NumberTitle', 'off');
        set(allchild(tab), 'Parent', out);
        flight = getappdata(tab, 'pprz_view_flight');
        delete(tab);
        if ~isempty(flight)
            pprz.markers(flight, out, event_kinds());
        end
        cursor.attach(out);
        refresh_tabs();
    end

    function close_tab(tab)
        if ~isempty(tab) && isvalid(tab)
            delete(tab);
        end
        refresh_tabs();
        show_values(cursor.time);
    end

    function close_all()
        delete(tabs.Children);
        refresh_tabs();
        show_values(cursor.time);
    end

    function save_png()
        tab = tabs.SelectedTab;
        if isempty(tab)
            status.Text = 'No plot to save.';
            return
        end
        [file, folder] = uiputfile('*.png', 'Save plot as PNG', [regexprep(tab.Title, '[^\w\-]+', '_') '.png']);
        figure(fig);
        if isequal(file, 0)
            return
        end
        try
            exportgraphics(tab, fullfile(folder, file), 'Resolution', 150);
            status.Text = sprintf('Saved %s.', file);
        catch err
            status.Text = err.message;
        end
    end

    % ---- event markers and the cursor panel
    function k = event_kinds()
        k = kinds([events.Value], 1)';
    end

    function update_markers()
        for tab = reshape(tabs.Children, 1, [])
            flight = getappdata(tab, 'pprz_view_flight');
            if ~isempty(flight)
                pprz.markers(flight, tab, event_kinds());
            end
        end
        show_values(cursor.time);
    end

    function show_values(t, query)
        if ~isvalid(fig)
            return
        end
        if nargin < 2
            query = signal_filter.Value;
        end
        if isnan(t)
            values.Data = cell(0, 3);
            near.Items = {};
            return
        end
        at.Text = sprintf('Cursor at %.2f s', t);
        v = cursor.values();
        if ~isempty(strtrim(query))
            v = v(contains(lower(strcat({v.plot}, ' ', {v.signal})), lower(strtrim(query))));
        end
        values.Data = [{v.plot}', {v.signal}', num2cell([v.value]')];
        ev = pprz.events(f, event_kinds());
        nearby = ev;
        if ~isempty(ev)
            [~, order] = sort(abs([ev.t] - t));
            nearby = ev(sort(order(1:min(8, end))));
        end
        near.Items = arrayfun(@(e) sprintf('%7.1f s · %s', e.t, e.label), nearby, 'UniformOutput', false);
        near.ItemsData = 1:numel(nearby);
        try  % no selection, so clicking the same event again still jumps
            near.Value = {};
        catch
        end
    end

    function jump_to_event()
        if ~isempty(near.Value)
            cursor.move(nearby(near.Value).t);
        end
    end

    % ---- analysis and comparison (their figures become tabs too)
    function replay()
        try
            rf = pprz_replay(f);
            cursor.listen(getappdata(rf, 'pprz_show'));  % the replay follows the linked cursor
            status.Text = '3-D replay opened in its own window; it follows the cursor.';
        catch err
            status.Text = err.message;
        end
    end

    function sysid()
        status.Text = 'Looking for identification experiments…';
        drawnow;
        try
            [ex, made] = pprz_sysid(f);
            for k = 1:numel(made)
                adopt(made(k), f);
            end
            apply_window();
            cursor.attach(fig);
            status.Text = sprintf('System ID: %d experiment(s) found; the table is in the command window.', numel(ex));
        catch err
            status.Text = err.message;
        end
    end

    function effectiveness()
        status.Text = 'Estimating control effectiveness…';
        drawnow;
        try
            [r, made] = pprz_effectiveness(f, 'range', time_window());
            adopt(made, f);
            cursor.attach(fig);
            status.Text = sprintf('Effectiveness from %s: R² %.2f / %.2f / %.2f (p, q, r).', r.input, r.fit);
        catch err
            status.Text = err.message;
        end
    end

    function compare()
        names = checked_plots();
        if isempty(names)
            status.Text = 'Tick the plots to compare.';
            return
        end
        d = uifigure('Name', 'Compare flights', 'Position', [fig.Position(1) + 60, fig.Position(2) + 200, 520 380], ...
            'WindowStyle', 'modal');
        cg2 = uigridlayout(d, [5 2], 'RowHeight', {'fit', '1x', 'fit', 'fit', 'fit'}, 'ColumnWidth', {'fit', '1x'});
        l = uilabel(cg2, 'Text', 'Flights (Ctrl/Cmd-click several)', 'FontWeight', 'bold');
        l.Layout.Column = [1 2];
        flights = uilistbox(cg2, 'Items', rows, 'ItemsData', 1:height(T), 'Multiselect', 'on', 'Value', pick.Value);
        flights.Layout.Column = [1 2];
        uilabel(cg2, 'Text', 'Align at');
        how = uidropdown(cg2, 'Items', {'Takeoff', 'First change of setting', 'First switch to mode'}, ...
            'ItemsData', {'takeoff', 'setting', 'mode'});
        uilabel(cg2, 'Text', 'Setting / mode');
        what = uieditfield(cg2, 'text', 'Placeholder', 'e.g. guidance_h.gain_p or NAV', ...
            'Tooltip', 'Name of the setting (as in the settings list) or autopilot mode');
        ok = uibutton(cg2, 'Text', 'Compare', 'ButtonPushedFcn', @(~, ~) run());
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
                made = pprz_compare(chosen, names{:}, 'align', align);
                for k = 1:numel(made)
                    adopt(made(k), []);  % several flights: no event markers
                end
                apply_window();
                cursor.attach(fig);
                status.Text = sprintf('Compared %d flight(s) in %d tab(s).', numel(chosen), numel(made));
            catch err
                status.Text = err.message;
            end
        end
    end
end

function label = heading(parent, text)
    label = uilabel(parent, 'Text', upper(text), 'FontWeight', 'bold', 'FontSize', 11, ...
        'FontColor', [0.35 0.35 0.35]);
end

function span = data_span(f)
    % Time span of the flight's data [s]: from the export window, else from the messages.
    if isfield(f.time, 'start_s') && all(isfinite([f.time.start_s, f.time.end_s, f.time.offset_s]))
        span = [f.time.start_s, f.time.end_s] - f.time.offset_s;
        return
    end
    span = [Inf -Inf];
    for name = reshape(fieldnames(f.data), 1, [])
        m = f.data.(name{1});
        if isstruct(m) && isfield(m, 't') && ~isempty(m.t)
            span = [min(span(1), min(m.t)), max(span(2), max(m.t))];
        end
    end
    if ~all(isfinite(span)) || span(2) <= span(1)
        span = [0 1];
    end
    span = [floor(span(1)), ceil(span(2))];
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

function v = ifelse(c, a, b)
    if c
        v = a;
    else
        v = b;
    end
end
