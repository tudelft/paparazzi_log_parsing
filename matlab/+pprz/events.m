function ev = events(f, kinds)
%EVENTS What happened during a flight, with times from takeoff (or log time for a whole log).
%   ev = pprz.events(f)                       all kinds
%   ev = pprz.events(f, {'mode', 'setting'})  only these kinds
%   Kinds: 'mode' (autopilot mode changes), 'setting' (setting changes), 'checklist' (checklist items) and
%   'message' (INFO_MSG). ev is a struct array, sorted by time, with t, kind, label (short) and text (full).
    if nargin < 2
        kinds = {'mode', 'setting', 'checklist', 'message'};
    end
    ev = struct('t', {}, 'kind', {}, 'label', {}, 'text', {});
    offset = 0;  % settings and checklist are in log time; flight data starts at takeoff
    if isfield(f, 'time') && isfield(f.time, 'offset_s') && isfinite(f.time.offset_s)
        offset = f.time.offset_s;
    end
    ac = struct();
    if isfield(f, 'aircraft')
        ac = f.aircraft;
    end

    if any(strcmp(kinds, 'mode'))
        for msg = {'ROTORCRAFT_STATUS', 'PPRZ_MODE'}
            if pprz.has(f.data, msg{1}) && isfield(f.data.(msg{1}), 'ap_mode')
                m = f.data.(msg{1});
                v = double(m.ap_mode(:));
                at = [1; find(diff(v) ~= 0) + 1];
                names = pprz.enum(f, msg{1}, 'ap_mode', v(at));
                for k = 1:numel(at)
                    name = char(names(k));
                    ev(end + 1) = event(m.t(at(k)), 'mode', name, ['Autopilot mode ' name]); %#ok<AGROW>
                end
                break
            end
        end
    end

    if any(strcmp(kinds, 'setting')) && isfield(ac, 'settings') && isfield(ac.settings, 'changes')
        ch = ac.settings.changes;
        for k = 1:numel(ch.t)
            name = cell_at(ch.name, k);
            ev(end + 1) = event(ch.t(k) - offset, 'setting', sprintf('%s = %g', name, ch.to(k)), ...
                sprintf('Setting %s: %g → %g', name, ch.from(k), ch.to(k))); %#ok<AGROW>
        end
    end

    if any(strcmp(kinds, 'checklist')) && isfield(ac, 'checklist') && isfield(ac.checklist, 'items')
        it = ac.checklist.items;
        for k = 1:numel(it.t)
            label = cell_at(it.item, k);
            answer = cell_at(it.answer, k);
            if ~isempty(answer)
                label = [label ': ' answer]; %#ok<AGROW>
            end
            ev(end + 1) = event(it.t(k) - offset, 'checklist', label, ['Checklist ' cell_at(it.message, k)]); %#ok<AGROW>
        end
    end

    if any(strcmp(kinds, 'message')) && pprz.has(f.data, 'INFO_MSG') && isfield(f.data.INFO_MSG, 'msg')
        m = f.data.INFO_MSG;
        for k = 1:numel(m.t)
            text = cell_at(m.msg, k);
            ev(end + 1) = event(m.t(k), 'message', text, text); %#ok<AGROW>
        end
    end

    if ~isempty(ev)
        [~, order] = sort([ev.t]);
        ev = ev(order);
        % Settings and checklist cover the whole log: keep what falls in this flight's window.
        if isfield(f, 'time') && all(isfield(f.time, {'start_s', 'end_s'})) && isfinite(f.time.start_s)
            in = [ev.t] >= f.time.start_s - offset & [ev.t] <= f.time.end_s - offset;
            ev = ev(in);
        end
    end
end

function e = event(t, kind, label, text)
    if numel(label) > 28
        label = [label(1:27) '…'];
    end
    e = struct('t', double(t), 'kind', kind, 'label', label, 'text', text);
end

function s = cell_at(c, k)
    s = '';
    if iscell(c) && k <= numel(c)
        s = char(c{k});
    end
end
