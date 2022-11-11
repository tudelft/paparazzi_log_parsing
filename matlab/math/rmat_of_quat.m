function [rmat] = rmat_of_quat(q)
%RMAT_OF_QUAT Summary of this function goes here
%   Detailed explanation goes here
  qi = q(1);
  qx = q(2);
  qy = q(3);
  qz = q(4);

  qi2_m1  = 2 * qi .^ 2 - 1;
  rmat(0 + 1) = 2 * qx .^ 2;
  rmat(4 + 1) = 2 * qy .^ 2;
  rmat(8 + 1) = 2 * qz .^ 2;

  qiqx = 2 * qi .* qx;
  qiqy = 2 * qi .* qy;
  qiqz = 2 * qi .* qz;
  rmat(1 + 1) = 2 * qx .* qy;
  rmat(2 + 1) = 2 * qx .* qz;
  rmat(5 + 1) = 2 * qy .* qz;
  rmat(0 + 1) = rmat(0 + 1) +  qi2_m1;
  rmat(3 + 1) = rmat(1 + 1) - qiqz;
  rmat(6 + 1) = rmat(2 + 1) + qiqy;
  rmat(7 + 1) = rmat(5 + 1) - qiqx;
  rmat(4 + 1) = rmat(4 + 1) +  qi2_m1;
  rmat(1 + 1) = rmat(1 + 1) +  qiqz;
  rmat(2 + 1) = rmat(2 + 1) -  qiqy;
  rmat(5 + 1) = rmat(5 + 1) +  qiqx;
  rmat(8 + 1) = rmat(8 + 1) +  qi2_m1;
  rmat = reshape(rmat, 3, 3);
end