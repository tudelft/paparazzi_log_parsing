function [values, names, t] = actuators(d)
%ACTUATORS The ACTUATORS message as an N×k matrix with its names and timestamps.
%   [values, names, t] = pprz.actuators(f.data)
%   Works whether the export stored named columns (ACTUATORS.FRONT_MOT) or a values matrix.
    m = d.ACTUATORS;
    t = m.t;
    if isfield(m, 'values')
        values = double(m.values);
        names = compose('ACT%d', 0:size(values, 2) - 1);
        return
    end
    names = setdiff(fieldnames(m), {'t'}, 'stable')';
    names = natural_sort(names);
    values = zeros(numel(t), numel(names));
    for k = 1:numel(names)
        values(:, k) = m.(names{k});
    end
end

function names = natural_sort(names)
    % MOTOR_2 before MOTOR_10: sort on the text with every number padded to the same width.
    keys = regexprep(regexprep(names, '(\d+)', '000000000$1'), '0*(\d{10})', '$1');
    [~, order] = sort(keys);
    names = names(order);
end
