function [b2c] = quat_inv_comp(a2b,a2c)
%UNTITLED Summary of this function goes here
%   Detailed explanation goes here
  b2c(:, 1) = a2b(:, 1) .* a2c(:, 1) + a2b(:, 2) .* a2c(:, 2) + a2b(:, 3) .* a2c(:, 3) + a2b(:, 4) .* a2c(:, 4);
  b2c(:, 2) = a2b(:, 1) .* a2c(:, 2) - a2b(:, 2) .* a2c(:, 1) - a2b(:, 3) .* a2c(:, 4) + a2b(:, 4) .* a2c(:, 3);
  b2c(:, 3) = a2b(:, 1) .* a2c(:, 3) + a2b(:, 2) .* a2c(:, 4) - a2b(:, 3) .* a2c(:, 1) - a2b(:, 4) .* a2c(:, 2);
  b2c(:, 4) = a2b(:, 1) .* a2c(:, 4) - a2b(:, 2) .* a2c(:, 3) + a2b(:, 3) .* a2c(:, 2) - a2b(:, 4) .* a2c(:, 1);
end

