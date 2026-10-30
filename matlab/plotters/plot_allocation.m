function plot_allocation(ac_data)
%PLOT_ALLOCATION WLS control allocation per loop (WLS_U, WLS_V; loop 'stab' or 'guid'): each actuator's
%   command scaled between its limits (0 = minimum, 1 = maximum, so saturation shows as 0 or 1), the virtual
%   control asked for, and the solver iterations.
    u = ac_data.WLS_U;
    loops = loop_names(u);
    has_v = pprz.has(ac_data, 'WLS_V');
    axs = pprz.tiles(numel(loops) * (1 + has_v) + has_v, 'Control allocation (WLS)');
    k = 0;
    for l = 1:numel(loops)
        rows = strcmp(loop_of(u), loops{l});
        span = double(u.u_max(rows, :)) - double(u.u_min(rows, :));
        span(span == 0) = NaN;
        use = (double(u.u(rows, :)) - double(u.u_min(rows, :))) ./ span;
        names = arrayfun(@(j) sprintf('u%d', j), 1:size(use, 2), 'UniformOutput', false);
        k = k + 1;
        pprz.series(axs(k), u.t(rows), use, names);
        set(axs(k), 'YLim', [-0.05 1.05]);
        ylabel(axs(k), sprintf('%s: actuator use', loops{l}), 'Interpreter', 'none');
        if has_v
            v = ac_data.WLS_V;
            vr = strcmp(loop_of(v), loops{l});
            names = virtual_names(loops{l}, size(v.v, 2));
            k = k + 1;
            pprz.series(axs(k), v.t(vr), double(v.v(vr, :)), names);
            ylabel(axs(k), sprintf('%s: virtual control', loops{l}), 'Interpreter', 'none');
        end
    end
    if has_v
        v = ac_data.WLS_V;
        for l = 1:numel(loops)
            vr = strcmp(loop_of(v), loops{l});
            stairs(axs(end), v.t(vr), double(v.iter(vr)), 'DisplayName', loops{l});
        end
        ylabel(axs(end), 'Iterations');
        legend(axs(end), 'Location', 'best', 'Interpreter', 'none');
    end
end

function names = virtual_names(loop, n)
    % stab: angular accelerations and thrust; guid: NED accelerations (and pusher); else numbered.
    known = struct('stab', {{'Roll accel', 'Pitch accel', 'Yaw accel', 'Thrust'}}, ...
        'guid', {{'North accel', 'East accel', 'Down accel', 'Forward'}});
    names = arrayfun(@(j) sprintf('v%d', j), 1:n, 'UniformOutput', false);
    if isfield(known, loop)
        k = min(n, numel(known.(loop)));
        names(1:k) = known.(loop)(1:k);
    end
end

function names = loop_names(m)
    names = unique(loop_of(m), 'stable');
end

function l = loop_of(m)
    % The loop name per row; one unnamed loop when the message has no loop text.
    if isfield(m, 'loop')
        l = strtrim(cellstr(m.loop));
    else
        l = repmat({'wls'}, numel(m.t), 1);
    end
    l(cellfun(@isempty, l)) = {'wls'};
end
