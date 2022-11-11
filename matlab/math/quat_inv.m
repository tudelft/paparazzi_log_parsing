function [q] = quat_inv(q)
%QUAT_INV Summary of this function goes here
%   Detailed explanation goes here
q(:, 2:4) = -q(:, 2:4);
end

