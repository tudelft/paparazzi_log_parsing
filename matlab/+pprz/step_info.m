function s = step_info(t, y, t0, t1)
%STEP_INFO Step response metrics of y for a step at t0, judged until t1.
%   s = pprz.step_info(t, y, t0, t1) gives initial and final value, delay (to 10 %), rise time
%   (10–90 %), overshoot [%] and settling time (within 5 %), all times in s after t0. NaN when y
%   hardly moved.
    t = t(:);
    y = double(y(:));
    before = t >= t0 - 0.5 & t < t0;
    if ~any(before)
        before = find(t < t0, 1, 'last');
    end
    after = t >= t0 & t <= t1;
    s = struct('initial', mean(y(before)), 'final', NaN, 'delay', NaN, 'rise', NaN, 'overshoot', NaN, ...
        'settling', NaN);
    if nnz(after) < 5 || isempty(before)
        return
    end
    ta = t(after);
    ya = y(after);
    tail = ta >= t0 + 0.7 * (t1 - t0);
    s.final = mean(ya(tail));
    span = s.final - s.initial;
    if abs(span) < 1e-9 || abs(span) < 3 * std(y(before))
        return
    end
    z = (ya - s.initial) / span;
    i10 = find(z >= 0.1, 1);
    i90 = find(z >= 0.9, 1);
    if ~isempty(i10)
        s.delay = ta(i10) - t0;
    end
    if ~isempty(i10) && ~isempty(i90)
        s.rise = ta(i90) - ta(i10);
    end
    s.overshoot = max(0, max(z) - 1) * 100;
    out = find(abs(z - 1) > 0.05, 1, 'last');
    if isempty(out)
        s.settling = 0;
    elseif out < numel(z)
        s.settling = ta(out + 1) - t0;
    end
end
