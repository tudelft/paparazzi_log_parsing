function [values, names, t] = actuators(d)
%ACTUATORS The ACTUATORS message as an N×k matrix with its names and timestamps.
%   [values, names, t] = pprz.actuators(f.data)
%   Works whether the export stored named columns (ACTUATORS.FRONT_MOT) or a values matrix.
    m = d.ACTUATORS;
    t = m.timestamp;
    if isfield(m, 'values')
        values = double(m.values);
        names = compose('ACT%d', 0:size(values, 2) - 1);
        return
    end
    names = setdiff(fieldnames(m), {'timestamp'}, 'stable')';
    values = zeros(numel(t), numel(names));
    for k = 1:numel(names)
        values(:, k) = m.(names{k});
    end
end
