function q = quat_of_axis_angle(uv, angle)

  san2 = sin(angle / 2);
  can2 = cos(angle / 2);
  
  q(:, 1) = can2;
  q(:, 2) = san2 * uv(:, 1);
  q(:, 3) = san2 * uv(:, 2);
  q(:, 4) = san2 * uv(:, 3);

end
