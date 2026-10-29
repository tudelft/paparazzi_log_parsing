function figs = pprz_compare(flights, varargin)
%PPRZ_COMPARE Overlay the same plots or signals of several flights, aligned at a common moment.
%   pprz_compare(flights, 'euler', 'actuators')         plots by name (see pprz_plot())
%   pprz_compare(flights, 'ROTORCRAFT_FP.up', 'AIR_DATA.airspeed')   signals 'MSG.field', one axes each
%   Options after the names:
%     'align'  'takeoff' (default; t = 0 is takeoff), {'setting', name} (first change of that setting),
%              {'mode', NAME} (first switch to that autopilot mode), or a vector of times, one per flight
%     'range'  [t0 t1] in s after alignment
%   flights is a cell array or struct array of flights from pprz_flight, or {logs, rows} with rows of
%   pprz_flights(logs). Each flight gets a colour; the signals of a plot keep apart by line style.
%   Returns the figures.
    if iscell(flights) && numel(flights) == 2 && isstruct(flights{1}) && isfield(flights{1}, 'aircraft') && ...
            isnumeric(flights{2})
        logs = flights{1};
        flights = arrayfun(@(r) pprz_flight(logs, r), flights{2}, 'UniformOutput', false);
    elseif isstruct(flights)
        flights = num2cell(flights);
    end
    names = {};
    opts = struct('align', 'takeoff', 'range', []);
    k = 1;
    while k <= numel(varargin)
        key = char(varargin{k});
        if isfield(opts, lower(key)) && k < numel(varargin)
            opts.(lower(key)) = varargin{k + 1};
            k = k + 2;
        else
            names{end + 1} = key; %#ok<AGROW>
            k = k + 1;
        end
    end
    if isempty(names)
        names = {'overview'};
    end
    shift = align_times(flights, opts.align);
    colors = lines(max(numel(flights), 1));
    labels = flight_labels(flights);
    figs = gobjects(0);
    dotted = ~cellfun(@isempty, strfind(names, '.'));
    signals = names(dotted);
    if ~isempty(signals)
        figs(end + 1) = compare_signals(flights, signals, shift, colors, labels, opts.range);
    end
    list = pprz_plots();
    for name = names(~dotted)
        p = list(strcmp({list.name}, name{1}));
        if isempty(p)
            error('pprz_compare:unknown', 'Unknown plot: %s. pprz_plot() lists them.', name{1});
        end
        figs(end + 1) = compare_plot(p, flights, shift, colors, labels, opts.range); %#ok<AGROW>
    end
end

function shift = align_times(flights, how)
    % The time of the common moment in each flight's own time (from takeoff); NaN when a flight lacks it.
    n = numel(flights);
    if isnumeric(how)
        shift = how(:)';
        if numel(shift) ~= n
            error('pprz_compare:align', 'Give one alignment time per flight (%d).', n);
        end
        return
    end
    shift = zeros(1, n);
    if ischar(how) || isstring(how)
        if ~strcmpi(how, 'takeoff')
            error('pprz_compare:align', 'align is ''takeoff'', {''setting'', name}, {''mode'', NAME} or times.');
        end
        return
    end
    [kind, name] = deal(lower(how{1}), char(how{2}));
    for k = 1:n
        f = flights{k};
        shift(k) = NaN;
        if strcmp(kind, 'setting') && isfield(f.aircraft, 'settings') && isfield(f.aircraft.settings, 'changes')
            ch = f.aircraft.settings.changes;
            inside = ch.t(:) >= f.time.start_s & ch.t(:) <= f.time.end_s;  % changes of this flight (log time)
            hit = find(strcmp(cellstr(ch.name(:)), name) & inside, 1);
            if ~isempty(hit)
                shift(k) = ch.t(hit) - f.time.offset_s;
            end
        elseif strcmp(kind, 'mode') && pprz.has(f.data, 'ROTORCRAFT_STATUS')
            m = f.data.ROTORCRAFT_STATUS;
            v = double(m.ap_mode(:));
            modes = pprz.enum(f, 'ROTORCRAFT_STATUS', 'ap_mode', v);
            into = find(strcmpi(cellstr(modes), name) & [true; diff(v) ~= 0], 1);
            if ~isempty(into)
                shift(k) = m.t(into);
            end
        end
    end
    missing = isnan(shift);
    if all(missing)
        error('pprz_compare:align', 'No flight has %s %s.', kind, name);
    elseif any(missing)
        warning('pprz_compare:align', 'Aligned at takeoff (no %s %s): %s', kind, name, ...
            strjoin(cellfun(@(f) char(f.title), flights(missing), 'UniformOutput', false), ', '));
        shift(missing) = 0;
    end
end

function fig = compare_signals(flights, signals, shift, colors, labels, range)
    fig = figure('Name', sprintf('Compare %d flights - signals', numel(flights)), 'NumberTitle', 'off');
    axs = gobjects(1, numel(signals));
    for s = 1:numel(signals)
        axs(s) = subplot(numel(signals), 1, s);
        hold(axs(s), 'on');
        grid(axs(s), 'on');
        parts = strsplit(signals{s}, '.');
        [msg, field] = deal(parts{1}, parts{2});
        for k = 1:numel(flights)
            d = flights{k}.data;
            if pprz.has(d, msg) && isfield(d.(msg), field)
                plot(axs(s), d.(msg).t - shift(k), double(d.(msg).(field)), 'Color', colors(k, :), ...
                    'DisplayName', labels{k});
            end
        end
        ylabel(axs(s), signals{s}, 'Interpreter', 'none');
        limit(axs(s), range);
    end
    legend(axs(1), 'Location', 'best', 'Interpreter', 'none');
    xlabel(axs(end), 'Time [s]');
    if numel(axs) > 1
        linkaxes(axs, 'x');
    end
end

function fig = compare_plot(p, flights, shift, colors, labels, range)
    % Draw the plot once per flight and gather the lines of each time axes into the first flight's figure.
    fig = figure('Name', sprintf('%s - compare %d flights', p.title, numel(flights)), 'NumberTitle', 'off');
    styles = {'-', '--', ':', '-.'};
    target = [];
    for k = 1:numel(flights)
        f = flights{k};
        if ~(isempty(p.needs) || any(cellfun(@(m) pprz.has(f.data, m), p.needs)))
            continue
        end
        if isempty(target)
            figure(fig);
            p.fn(f.data, f);
            target = pprz.time_axes(fig);
            source = target;
        else
            scratch = figure('Visible', 'off');
            p.fn(f.data, f);
            source = pprz.time_axes(scratch);
        end
        for a = 1:min(numel(source), numel(target))
            j = 0;
            for side = y_sides(source(a))
                if ~isempty(side{1})
                    yyaxis(source(a), side{1});
                    yyaxis(target(a), side{1});
                end
                kids = flipud(get(source(a), 'Children'));  % this y side only, in creation order
                lns = kids(ismember(get(kids, {'Type'}), {'line', 'stair'}));
                if source(a) ~= target(a)
                    lns = copyobj(lns, target(a));
                end
                for i = 1:numel(lns)
                    j = j + 1;
                    name = strrep(char(get(lns(i), 'DisplayName')), '\_', '_');
                    if isempty(name) || strncmp(name, 'data', 4)
                        name = sprintf('line %d', j);
                    end
                    set(lns(i), 'XData', get(lns(i), 'XData') - shift(k), 'Color', colors(k, :), ...
                        'LineStyle', styles{mod(j - 1, numel(styles)) + 1}, 'Marker', 'none', ...
                        'DisplayName', sprintf('%s · %s', name, labels{k}));
                end
            end
        end
        if source(1) ~= target(1)
            close(scratch);
        end
    end
    if isempty(target)
        close(fig);
        error('pprz_compare:missing', '%s: none of these flights logged %s.', p.title, strjoin(p.needs, '/'));
    end
    old = findobj(fig, 'Type', 'legend');  % the per-flight names replace them
    if isempty(old)
        old = findobj(fig, 'Tag', 'legend');  % Octave
    end
    delete([old; findobj(fig, 'Type', 'datatip')]);
    set(findobj(fig, 'Type', 'subplottext'), 'String', get(fig, 'Name'));  % sgtitle named the first flight
    for tl = reshape(findobj(fig, 'Type', 'tiledlayout'), 1, [])
        set(get(tl, 'Title'), 'String', get(fig, 'Name'));
    end
    for a = 1:numel(target)
        limit(target(a), range);
    end
    % One legend of the flights' colours on the top axes.
    hold(target(1), 'on');
    keys = gobjects(1, numel(flights));
    for k = 1:numel(flights)
        keys(k) = plot(target(1), NaN, NaN, '-', 'Color', colors(k, :), 'LineWidth', 2, 'DisplayName', labels{k});
    end
    legend(target(1), keys, 'Location', 'best', 'Interpreter', 'none');
    if numel(target) > 1
        linkaxes(target, 'x');
    end
end

function limit(ax, range)
    % range may leave one end open (-Inf/Inf).
    set(ax, 'XLimMode', 'auto');
    if ~isempty(range) && any(isfinite(range))
        xl = get(ax, 'XLim');
        range(~isfinite(range)) = xl(~isfinite(range));
        set(ax, 'XLim', range);
    end
end

function sides = y_sides(ax)
    % Both sides of a yyaxis plot, else the one (yyaxis is MATLAB only).
    sides = {''};
    try
        if numel(get(ax, 'YAxis')) > 1
            sides = {'left', 'right'};
        end
    catch
    end
end

function labels = flight_labels(flights)
    % 'aircraft · flight N', with the file only when the flights come from different files.
    parts = cellfun(@(f) strtrim(strsplit(char(f.title), '·')), flights, 'UniformOutput', false);
    files = cellfun(@(p) p{1}, parts, 'UniformOutput', false);
    labels = cell(size(flights));
    for k = 1:numel(flights)
        rest = parts{k}(2:end);
        rest = rest(~cellfun(@isempty, rest));
        labels{k} = strjoin(rest, ' · ');
        if isempty(rest)
            labels{k} = files{k};
        elseif numel(unique(files)) > 1
            labels{k} = sprintf('%s (%s)', labels{k}, files{k});
        end
    end
end
