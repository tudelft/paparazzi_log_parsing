function figs = pprz_plot(f, varargin)
%PPRZ_PLOT Plot a flight from pprz_flight.
%   pprz_plot(f, 'overview', 'euler', 'actuators')   these plots (names from pprz_plot())
%   pprz_plot(f)                                     every plot whose messages were logged
%   pprz_plot()                                      list the plots
%   Options after the names: 'modes' ('ap' autopilot mode, 'rc' RC mode, 'none'), 'range' [t0 t1] in s,
%   'docked' (true: figures as tabs in one window), 'events' (true: mode/setting/checklist/message markers,
%   or a list of those kinds such as {'mode', 'message'}; see pprz.markers), 'visible' (false: draw the figures
%   hidden, e.g. to move their contents elsewhere). Returns the figures.
    list = pprz_plots();
    if nargin == 0
        disp(struct2table(rmfield(list, 'fn')));
        return
    end
    names = {};
    opts = struct('modes', 'none', 'range', [], 'docked', false, 'events', false, 'visible', true);
    k = 1;
    while k <= numel(varargin)
        key = lower(char(varargin{k}));
        if isfield(opts, key) && k < numel(varargin)
            opts.(key) = varargin{k + 1};
            k = k + 2;
        else
            names{end + 1} = key; %#ok<AGROW>
            k = k + 1;
        end
    end
    if ~isempty(opts.range)
        f.data = pprz.crop(f.data, opts.range(1), opts.range(2));
    end
    available = arrayfun(@(p) isempty(p.needs) || any(cellfun(@(m) pprz.has(f.data, m), p.needs)), list);
    if isempty(names)
        chosen = find(available)';
    else
        [found, chosen] = ismember(names, {list.name});
        if ~all(found)
            error('pprz_plot:unknown', 'Unknown plot: %s. pprz_plot() lists them.', strjoin(names(~found), ', '));
        end
    end
    figs = gobjects(0);
    for i = chosen
        if ~available(i)
            warning('pprz_plot:missing', '%s: %s not logged.', list(i).title, strjoin(list(i).needs, '/'));
            continue
        end
        visible = 'on';
        if ~opts.visible
            visible = 'off';
        end
        fig = figure('Name', sprintf('%s - %s', list(i).title, f.title), 'NumberTitle', 'off', 'Visible', visible);
        if opts.docked
            fig.WindowStyle = 'docked';
        end
        try
            list(i).fn(f.data, f);
            link = pprz.time_axes(fig);
            if numel(link) > 1
                linkaxes(link, 'x');
            end
            if ~strcmp(opts.modes, 'none')
                pprz.modes(f, fig, opts.modes);
            end
            if iscell(opts.events) || isstring(opts.events)
                if ~isempty(opts.events)
                    pprz.markers(f, fig, cellstr(opts.events));
                end
            elseif opts.events
                pprz.markers(f, fig);
            end
            figs(end + 1) = fig; %#ok<AGROW>
        catch err
            close(fig);
            warning('pprz_plot:failed', '%s failed: %s', list(i).title, err.message);
        end
    end
end
