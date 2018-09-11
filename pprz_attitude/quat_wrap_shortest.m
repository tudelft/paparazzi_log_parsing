function q = quat_wrap_shortest(q)

indices = q(:, 1) < 0;

q(indices, :) = -q(indices, :);

end