function plot_settings(~, f)
%PLOT_SETTINGS Values of the settings that changed during the flight, against time from takeoff.
    if nargin < 2 || ~isfield(f.aircraft, 'settings') || ~isfield(f.aircraft.settings, 'values')
        return
    end
    values = f.aircraft.settings.values;
    names = fieldnames(values);
    [t0, t1, offset] = deal(-Inf, Inf, 0);
    if isfield(f.time, 'start_s') && isfinite(f.time.start_s)
        [t0, t1, offset] = deal(f.time.start_s, f.time.end_s, f.time.offset_s);
    end
    labels = names;  % the setting names with dots, when the list matches
    try
        vars = cellstr(f.aircraft.settings.list.var);
        if numel(vars) == numel(names)
            labels = vars;
        end
    catch
    end
    changed = struct('name', {}, 't', {}, 'v', {});
    for i = 1:numel(names)
        v = values.(names{i});
        t = v.t(:);
        x = double(v.value(:));
        in = t >= t0 & t <= t1;
        before = find(t < t0, 1, 'last');  % the value the window starts with
        if ~isempty(before)
            in(before) = true;
            t(before) = t0;
        end
        if numel(unique(x(in))) > 1
            changed(end + 1) = struct('name', labels{i}, 't', [t(in); t1] - offset, 'v', [x(in); x(find(in, 1, 'last'))]); %#ok<AGROW>
        end
    end
    if isempty(changed)
        text(0.5, 0.5, 'No setting changed in this flight', 'HorizontalAlignment', 'center');
        axis off;
        return
    end
    axs = pprz.tiles(numel(changed), 'Setting changes');
    for i = 1:numel(changed)
        c = changed(i);
        last = c.t;
        last(~isfinite(last)) = max(c.t(isfinite(c.t)));
        stairs(axs(i), last, c.v, 'LineWidth', 1.2, 'DisplayName', c.name);
        ylabel(axs(i), c.name, 'Interpreter', 'none');
    end
end
