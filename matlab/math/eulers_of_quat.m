function eulers = eulers_of_quat(q)
   qx2  = q(:, 2) .* q(:, 2);
   qy2  = q(:, 3) .* q(:, 3);
   qz2  = q(:, 4) .* q(:, 4);
   qix = q(:, 1) .* q(:, 2);
   qiy = q(:, 1) .* q(:, 3);
   qiz = q(:, 1) .* q(:, 4);
   qxy = q(:, 2) .* q(:, 3);
   qxz = q(:, 2) .* q(:, 4);
   qyz = q(:, 3) .* q(:, 4);
   dcm00 = 1.0 - 2.*(qy2 +  qz2);
   dcm01 =       2.*(qxy + qiz);
   dcm02 =       2.*(qxz - qiy);
   dcm12 =       2.*(qyz + qix);
   dcm22 = 1.0 - 2.*(qx2 +  qy2);

  phi = atan2(dcm12, dcm22);
  theta = -asin(dcm02);
  psi = atan2(dcm01, dcm00);
  eulers = [phi theta psi];
end
